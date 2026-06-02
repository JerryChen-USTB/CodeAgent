from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
sys.path.insert(0, str(WORKSPACE))


class MeetingRoomBookingOracleTests(unittest.TestCase):
    def make_client(self):
        from meeting_room_booking import create_app

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = create_app(db_path=str(Path(tmp.name) / "booking.db"))
        app.config.update(TESTING=True)
        return app.test_client()

    def create_room(self, client, name: str = "Room A", capacity: int = 8):
        return client.post("/rooms", json={"name": name, "capacity": capacity, "location": "2F"})

    def test_health_rooms_and_duplicate_validation(self) -> None:
        client = self.make_client()
        health = client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json(), {"status": "ok"})

        created = self.create_room(client)
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self.assertEqual(created.get_json()["name"], "Room A")

        rooms = client.get("/rooms")
        self.assertEqual(rooms.status_code, 200)
        self.assertEqual([room["name"] for room in rooms.get_json()], ["Room A"])

        duplicate = self.create_room(client)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.get_json()["error"], "room already exists")

        invalid = client.post("/rooms", json={"name": "Bad", "capacity": 0})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("error", invalid.get_json())

    def test_booking_conflict_boundary_cancel_and_query(self) -> None:
        client = self.make_client()
        room = self.create_room(client).get_json()
        other_room = self.create_room(client, "Room B", 4).get_json()

        first = client.post(
            "/bookings",
            json={
                "room_id": room["id"],
                "user": "Ada",
                "title": "Weekly Sync",
                "start": "2026-06-10 09:00",
                "end": "2026-06-10 10:00",
            },
        )
        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        booking_id = first.get_json()["id"]

        conflict = client.post(
            "/bookings",
            json={
                "room_id": room["id"],
                "user": "Bob",
                "title": "Overlap",
                "start": "2026-06-10 09:30",
                "end": "2026-06-10 10:30",
            },
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["error"], "booking conflict")

        boundary = client.post(
            "/bookings",
            json={
                "room_id": room["id"],
                "user": "Bob",
                "title": "Next",
                "start": "2026-06-10 10:00",
                "end": "2026-06-10 11:00",
            },
        )
        self.assertEqual(boundary.status_code, 201, boundary.get_data(as_text=True))

        other = client.post(
            "/bookings",
            json={
                "room_id": other_room["id"],
                "user": "Chen",
                "title": "Other room",
                "start": "2026-06-10 09:30",
                "end": "2026-06-10 10:30",
            },
        )
        self.assertEqual(other.status_code, 201, other.get_data(as_text=True))

        room_bookings = client.get(f"/bookings?date=2026-06-10&room_id={room['id']}")
        self.assertEqual(room_bookings.status_code, 200)
        self.assertEqual([booking["title"] for booking in room_bookings.get_json()], ["Weekly Sync", "Next"])

        cancelled = client.delete(f"/bookings/{booking_id}")
        self.assertEqual(cancelled.status_code, 204)
        self.assertEqual(client.delete(f"/bookings/{booking_id}").status_code, 404)

        replacement = client.post(
            "/bookings",
            json={
                "room_id": room["id"],
                "user": "Dana",
                "title": "Replacement",
                "start": "2026-06-10 09:15",
                "end": "2026-06-10 09:45",
            },
        )
        self.assertEqual(replacement.status_code, 201, replacement.get_data(as_text=True))

    def test_booking_validation_errors(self) -> None:
        client = self.make_client()
        missing_room = client.post(
            "/bookings",
            json={"room_id": 999, "user": "Ada", "title": "Ghost", "start": "2026-06-10 09:00", "end": "2026-06-10 10:00"},
        )
        self.assertEqual(missing_room.status_code, 404)
        self.assertEqual(missing_room.get_json()["error"], "room not found")

        room = self.create_room(client).get_json()
        bad_time = client.post(
            "/bookings",
            json={"room_id": room["id"], "user": "Ada", "title": "Bad", "start": "2026-06-10 10:00", "end": "2026-06-10 09:00"},
        )
        self.assertEqual(bad_time.status_code, 400)
        self.assertIn("error", bad_time.get_json())


if __name__ == "__main__":
    unittest.main()
