import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from src import database

TOKEN = "user-access-token"


class SupabaseClientThreadIsolationTests(unittest.TestCase):
    """A Supabase client (HTTP/2 underneath) must never be shared by concurrently running threads."""

    def setUp(self):
        database._thread_clients.__dict__.clear()
        self.created = []

        def fake_create_client(url, key):
            client = MagicMock(name=f"client-{len(self.created)}")
            self.created.append(client)
            return client

        for patcher in (
            patch.object(database, "create_client", side_effect=fake_create_client),
            patch.object(database, "load_supabase_env", return_value=("https://example.supabase.co", "anon-key")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(database._thread_clients.__dict__.clear)

    def test_concurrent_calls_get_one_client_per_thread(self):
        # Same shape as load_profile_materials: four loaders in parallel with the same user token.
        barrier = threading.Barrier(4)

        def load(_):
            barrier.wait(timeout=5)  # all four threads are alive at the same time
            return threading.get_ident(), database.get_client(access_token=TOKEN, refresh_token="refresh")

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(load, range(4)))

        thread_ids = {thread_id for thread_id, _ in results}
        client_ids = {id(client) for _, client in results}
        self.assertEqual(len(thread_ids), 4)
        self.assertEqual(len(client_ids), 4, "a Supabase client was shared across worker threads")

    def test_access_token_is_still_applied_to_each_client(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            clients = list(pool.map(lambda _: database.get_client(access_token=TOKEN), range(4)))
        for client in clients:
            client.postgrest.auth.assert_called_once_with(TOKEN)

    def test_no_token_means_no_auth_header_override(self):
        client = database.get_client()
        client.postgrest.auth.assert_not_called()

    def test_same_thread_reuses_its_client_and_separates_tokens(self):
        first = database.get_client(access_token=TOKEN)
        self.assertIs(database.get_client(access_token=TOKEN), first)
        self.assertIsNot(database.get_client(access_token="another-users-token"), first)
        self.assertEqual(len(self.created), 2)

    def test_real_loaders_in_parallel_use_distinct_clients(self):
        used_by_thread = {}
        lock = threading.Lock()
        barrier = threading.Barrier(4)

        def record_table(client):
            def table(name):
                with lock:
                    used_by_thread.setdefault(threading.get_ident(), set()).add(id(client))
                return MagicMock(**{"select.return_value.eq.return_value.execute.return_value.data": []})
            return table

        def fake_create_client(url, key):
            client = MagicMock()
            client.table.side_effect = record_table(client)
            self.created.append(client)
            return client

        loaders = [database.get_resumes_for_user, database.get_cover_letters_for_user, database.get_resumes_for_user, database.get_cover_letters_for_user]

        def run(load):
            barrier.wait(timeout=5)
            return load("user-1", access_token=TOKEN, refresh_token="refresh")

        with patch.object(database, "create_client", side_effect=fake_create_client), ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, loaders))

        all_clients = [client_id for clients in used_by_thread.values() for client_id in clients]
        self.assertEqual(len(used_by_thread), 4)
        self.assertEqual(len(all_clients), len(set(all_clients)), "two threads queried through the same client")


if __name__ == "__main__":
    unittest.main()
