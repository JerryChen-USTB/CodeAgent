from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
sys.path.insert(0, str(WORKSPACE))


class MeetingRoomBookingWebOracleTests(unittest.TestCase):
    def make_client(self, db_path: Path | None = None):
        from meeting_room_booking import create_app

        if db_path is None:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            db_path = Path(tmp.name) / "booking.db"
        app = create_app(db_path=str(db_path))
        app.config.update(TESTING=True)
        return app.test_client()

    def response_text(self, response) -> str:
        return response.get_data(as_text=True)

    def create_room_api(self, client, name: str = "Room A", capacity: int = 8):
        return client.post("/rooms", json={"name": name, "capacity": capacity, "location": "2F"})

    def create_booking_api(self, client, room_id: int, **overrides):
        payload = {
            "room_id": room_id,
            "user": "Ada",
            "title": "Weekly Sync",
            "start": "2026-06-10 09:00",
            "end": "2026-06-10 10:00",
        }
        payload.update(overrides)
        return client.post("/bookings", json=payload)

    def test_json_api_contract_conflicts_cancellation_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "booking.db"
            client = self.make_client(db_path)

            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.get_json(), {"status": "ok"})

            created = self.create_room_api(client)
            self.assertEqual(created.status_code, 201, self.response_text(created))
            room = created.get_json()
            self.assertEqual(room["name"], "Room A")

            duplicate = self.create_room_api(client)
            self.assertEqual(duplicate.status_code, 409)
            self.assertEqual(duplicate.get_json()["error"], "room already exists")

            invalid = client.post("/rooms", json={"name": "Bad", "capacity": 0})
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(invalid.get_json()["error"], "invalid capacity")

            other_room = self.create_room_api(client, "Room B", 4).get_json()

            first = self.create_booking_api(client, room["id"])
            self.assertEqual(first.status_code, 201, self.response_text(first))
            first_booking = first.get_json()
            self.assertEqual(first_booking["status"], "active")
            self.assertEqual(first_booking["title"], "Weekly Sync")

            conflict = self.create_booking_api(
                client,
                room["id"],
                user="Bob",
                title="Overlap",
                start="2026-06-10 09:30",
                end="2026-06-10 10:30",
            )
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.get_json()["error"], "booking conflict")

            boundary = self.create_booking_api(
                client,
                room["id"],
                user="Bob",
                title="Next",
                start="2026-06-10 10:00",
                end="2026-06-10 11:00",
            )
            self.assertEqual(boundary.status_code, 201, self.response_text(boundary))

            other = self.create_booking_api(
                client,
                other_room["id"],
                user="Chen",
                title="Other room",
                start="2026-06-10 09:30",
                end="2026-06-10 10:30",
            )
            self.assertEqual(other.status_code, 201, self.response_text(other))

            room_bookings = client.get(f"/bookings?date=2026-06-10&room_id={room['id']}")
            self.assertEqual(room_bookings.status_code, 200)
            self.assertEqual([booking["title"] for booking in room_bookings.get_json()], ["Weekly Sync", "Next"])

            cancelled = client.delete(f"/bookings/{first_booking['id']}")
            self.assertEqual(cancelled.status_code, 204)

            cancelled_again = client.delete(f"/bookings/{first_booking['id']}")
            self.assertEqual(cancelled_again.status_code, 404)
            self.assertEqual(cancelled_again.get_json()["error"], "booking not found")

            replacement = self.create_booking_api(
                client,
                room["id"],
                user="Dana",
                title="Replacement",
                start="2026-06-10 09:15",
                end="2026-06-10 09:45",
            )
            self.assertEqual(replacement.status_code, 201, self.response_text(replacement))

            restarted = self.make_client(db_path)
            rooms = restarted.get("/rooms")
            self.assertEqual(rooms.status_code, 200)
            self.assertEqual([item["name"] for item in rooms.get_json()], ["Room A", "Room B"])

            persisted = restarted.get(f"/bookings?date=2026-06-10&room_id={room['id']}")
            self.assertEqual(persisted.status_code, 200)
            self.assertEqual([booking["title"] for booking in persisted.get_json()], ["Replacement", "Next"])

    def test_web_ui_forms_listing_cancellation_and_errors(self) -> None:
        client = self.make_client()

        home = client.get("/")
        self.assertEqual(home.status_code, 200)
        home_text = self.response_text(home)
        self.assertIn("会议室预约系统", home_text)
        for label in ["会议室", "预约"]:
            self.assertIn(label, home_text)

        created_room = client.post(
            "/ui/rooms",
            data={"name": "Room A", "capacity": "8", "location": "2F"},
            follow_redirects=True,
        )
        self.assertLess(created_room.status_code, 400, self.response_text(created_room))
        self.assertIn("Room A", self.response_text(created_room))

        rooms_page = client.get("/ui/rooms")
        self.assertEqual(rooms_page.status_code, 200)
        rooms_text = self.response_text(rooms_page)
        self.assertIn("Room A", rooms_text)
        self.assertIn("2F", rooms_text)

        room_id = client.get("/rooms").get_json()[0]["id"]

        created_booking = client.post(
            "/ui/bookings",
            data={
                "room_id": str(room_id),
                "user": "Ada",
                "title": "Weekly Sync",
                "start": "2026-06-10 09:00",
                "end": "2026-06-10 10:00",
            },
            follow_redirects=True,
        )
        self.assertLess(created_booking.status_code, 400, self.response_text(created_booking))
        booking_text = self.response_text(created_booking)
        self.assertIn("Weekly Sync", booking_text)
        self.assertIn("Ada", booking_text)
        self.assertIn("2026-06-10 09:00", booking_text)

        filtered = client.get(f"/ui/bookings?date=2026-06-10&room_id={room_id}")
        self.assertEqual(filtered.status_code, 200)
        self.assertIn("Weekly Sync", self.response_text(filtered))

        conflict = client.post(
            "/ui/bookings",
            data={
                "room_id": str(room_id),
                "user": "Bob",
                "title": "Overlap",
                "start": "2026-06-10 09:30",
                "end": "2026-06-10 10:30",
            },
            follow_redirects=True,
        )
        self.assertIn("booking conflict", self.response_text(conflict))

        booking_id = client.get("/bookings?date=2026-06-10").get_json()[0]["id"]
        cancelled = client.post(f"/ui/bookings/{booking_id}/cancel", follow_redirects=True)
        self.assertLess(cancelled.status_code, 400, self.response_text(cancelled))

        after_cancel = client.get(f"/ui/bookings?date=2026-06-10&room_id={room_id}")
        self.assertNotIn("Weekly Sync", self.response_text(after_cancel))

        replacement = client.post(
            "/ui/bookings",
            data={
                "room_id": str(room_id),
                "user": "Dana",
                "title": "Replacement",
                "start": "2026-06-10 09:15",
                "end": "2026-06-10 09:45",
            },
            follow_redirects=True,
        )
        self.assertLess(replacement.status_code, 400, self.response_text(replacement))
        self.assertIn("Replacement", self.response_text(replacement))

        duplicate_room = client.post(
            "/ui/rooms",
            data={"name": "Room A", "capacity": "8", "location": "2F"},
            follow_redirects=True,
        )
        self.assertIn("room already exists", self.response_text(duplicate_room))

        bad_time = client.post(
            "/ui/bookings",
            data={
                "room_id": str(room_id),
                "user": "Ada",
                "title": "Bad",
                "start": "2026-06-10 10:00",
                "end": "2026-06-10 09:00",
            },
            follow_redirects=True,
        )
        self.assertIn("invalid time", self.response_text(bad_time))

    def test_json_validation_errors(self) -> None:
        client = self.make_client()

        not_json = client.post("/rooms", data="not-json", content_type="text/plain")
        self.assertEqual(not_json.status_code, 400)
        self.assertIn("error", not_json.get_json())

        missing_field = client.post("/bookings", json={"room_id": 1})
        self.assertEqual(missing_field.status_code, 400)
        self.assertEqual(missing_field.get_json()["error"], "missing field")

        missing_room = client.post(
            "/bookings",
            json={
                "room_id": 999,
                "user": "Ada",
                "title": "Ghost",
                "start": "2026-06-10 09:00",
                "end": "2026-06-10 10:00",
            },
        )
        self.assertEqual(missing_room.status_code, 404)
        self.assertEqual(missing_room.get_json()["error"], "room not found")

        room = self.create_room_api(client).get_json()
        bad_time = self.create_booking_api(
            client,
            room["id"],
            title="Bad",
            start="2026-06-10 10:00",
            end="2026-06-10 09:00",
        )
        self.assertEqual(bad_time.status_code, 400)
        self.assertEqual(bad_time.get_json()["error"], "invalid time")


if __name__ == "__main__":
    unittest.main()
