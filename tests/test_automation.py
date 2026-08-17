import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import comments
import publisher
import weather


class WeatherTests(unittest.TestCase):
    def test_daily_forecasts_and_vk_draft(self):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        timeseries = []
        for offset, temperature, symbol in (
            (0, 12.2, "cloudy"),
            (3, 18.6, "lightrain_day"),
            (24, 9.1, "fair_day"),
            (30, 20.2, "partlycloudy_day"),
        ):
            moment = now + timedelta(hours=offset)
            timeseries.append({
                "time": moment.isoformat().replace("+00:00", "Z"),
                "data": {
                    "instant": {"details": {"air_temperature": temperature, "wind_speed": 3.4}},
                    "next_1_hours": {
                        "summary": {"symbol_code": symbol},
                        "details": {"precipitation_amount": 0.2},
                    },
                },
            })
        days = weather.daily_forecasts({"properties": {"timeseries": timeseries}})
        self.assertEqual(len(days), 2)
        draft = weather.render_vk(days)
        self.assertIn("ПОГОДА В ТУТАЕВЕ", draft)
        self.assertIn("Данные: MET Norway", draft)


class CommentTests(unittest.TestCase):
    def test_checkpoint_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"last_comment_timestamp": 123}), encoding="utf-8")
            self.assertEqual(comments.load_checkpoint(path), 123)

    def test_normalize_comment(self):
        item = comments.normalize_comment(
            {
                "id": 42,
                "from_id": 7,
                "date": 1_700_000_000,
                "text": "Вопрос",
                "likes": {"count": 3},
                "attachments": [{"type": "photo"}],
                "thread": {"count": 2},
            },
            -70948047,
            100,
            "Заголовок поста",
            {7: {"first_name": "Иван", "last_name": "Иванов"}},
        )
        self.assertEqual(item["author"], "Иван Иванов")
        self.assertEqual(item["attachments"], ["photo"])
        self.assertTrue(item["url"].endswith("?reply=42"))


class PublisherRetryTests(unittest.TestCase):
    @patch("publisher.time.sleep")
    @patch("publisher.urlopen")
    def test_retries_vk_internal_error(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = [
            io.StringIO('{"error":{"error_code":10,"error_msg":"try later"}}'),
            io.StringIO('{"response":{"id":70948047}}'),
        ]

        response = publisher.api_call("groups.getById", "token", group_ids="vtutaeve")

        self.assertEqual(response, {"id": 70948047})
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
