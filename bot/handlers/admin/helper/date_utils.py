from datetime import datetime
from datetime import timedelta, time as dtime, date
from typing import List


def all_30min_slots_for_date(selected_date: date) -> List[datetime]:
    slots = []
    slot_start = datetime.combine(selected_date, dtime(hour=11, minute=0))
    slot_end = datetime.combine(selected_date, dtime(hour=23, minute=0))
    while slot_start < slot_end:
        slots.append(slot_start)
        slot_start += timedelta(minutes=30)
    return slots

