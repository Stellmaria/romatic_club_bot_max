from datetime import datetime


def slot_allowed_for_user(dt: datetime, is_luxury: bool) -> bool:
    if is_luxury:
        return 11 <= dt.hour < 23 or (dt.hour == 22 and dt.minute == 30)
    else:
        return 11 <= dt.hour < 20 or (dt.hour == 20 and dt.minute in (0, 30))
