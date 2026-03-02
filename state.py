# state.py
import json
import logging
import threading
import time

log = logging.getLogger("parking")

# ---- Data model for one lot ----
class LotState:
    def __init__(self, lotId, capacity: int):
        self.lotId = lotId
        self.capacity = int(capacity)
        self.occupiedPhysical = 0
        # plate -> expires_at (unix seconds)
        self.reservations = {}


class ParkingState:
    """In-memory parking state.

    Thread-safety:- All mutations and reads that depend on multiple fields are protected by self.lock.
    """

    def __init__(self, lots, reservationTtlSec: int):
        # lock protects all shared state changes (reserve/cancel/sensor/expire)
        self.lock = threading.RLock()
        self.ttl = int(reservationTtlSec)
        self.lots = {}
        for lotId, cap in lots.items():
            self.lots[lotId] = LotState(lotId, int(cap))

    def freeCountLocked(self, lot: LotState) -> int:
        return lot.capacity - lot.occupiedPhysical - len(lot.reservations)

    def allLots(self):
        with self.lock:
            result = []
            for lot in self.lots.values():
                free = self.freeCountLocked(lot)
                occupiedTotal = lot.occupiedPhysical + len(lot.reservations)
                result.append(
                    {"id": lot.lotId, "capacity": lot.capacity, "occupied": occupiedTotal, "free": free}
                )
            return result

    def availability(self, lotId: str) -> int:
        with self.lock:
            if lotId not in self.lots:
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            return self.freeCountLocked(lot)

    def reserve(self, lotId: str, plate: str):
        """Attempt to reserve a spot for plate in lotId.

        Returns: (ok:bool, status:str, changed_free:bool)
        status in {OK, FULL, EXISTS}
        """
        with self.lock:
            if lotId not in self.lots:
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            freeBefore = self.freeCountLocked(lot)

            if plate in lot.reservations:
                return False, "EXISTS", False
            if freeBefore <= 0:
                return False, "FULL", False

            lot.reservations[plate] = time.time() + self.ttl
            freeAfter = self.freeCountLocked(lot)

            log.info(
                json.dumps(
                    {
                        "type": "reserve",
                        "lotId": lotId,
                        "plate": plate,
                        "ts": int(time.time() * 1000),
                        "result": "OK",
                    }
                )
            )
            return True, "OK", freeAfter != freeBefore

    def cancel(self, lotId: str, plate: str):
        """Cancel an existing reservation.

        Returns: (ok:bool, status:str, changed_free:bool)
        status in {OK, NOT_FOUND}
        """
        with self.lock:
            if lotId not in self.lots:
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            freeBefore = self.freeCountLocked(lot)

            if plate not in lot.reservations:
                return False, "NOT_FOUND", False

            del lot.reservations[plate]
            freeAfter = self.freeCountLocked(lot)

            log.info(
                json.dumps(
                    {
                        "type": "cancel",
                        "lotId": lotId,
                        "plate": plate,
                        "ts": int(time.time() * 1000),
                        "result": "OK",
                    }
                )
            )
            return True, "OK", freeAfter != freeBefore

    def applySensorUpdate(self, lotId: str, delta: int):
        """Apply UPDATE <lotId> <delta>.

        Returns: (free_after:int, changed_free:bool)
        """
        with self.lock:
            if lotId not in self.lots:
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            freeBefore = self.freeCountLocked(lot)

            lot.occupiedPhysical += int(delta)
            if lot.occupiedPhysical < 0:
                lot.occupiedPhysical = 0

            # Clamp so physical occupancy can't exceed capacity minus active reservations
            maxPhysical = max(0, lot.capacity - len(lot.reservations))
            if lot.occupiedPhysical > maxPhysical:
                lot.occupiedPhysical = maxPhysical

            freeAfter = self.freeCountLocked(lot)
            log.info(
                json.dumps(
                    {
                        "type": "sensor_update",
                        "lotId": lotId,
                        "delta": int(delta),
                        "occupiedPhysical": lot.occupiedPhysical,
                        "ts": int(time.time() * 1000),
                    }
                )
            )
            return freeAfter, freeAfter != freeBefore

    def expireOnce(self):
        """Expire any reservations past their TTL.

        Returns: list[(lotId, free_after)] for lots whose free count changed.
        """
        now = time.time()
        changed = []
        with self.lock:
            for lot in self.lots.values():
                freeBefore = self.freeCountLocked(lot)
                expired = [p for p, exp in lot.reservations.items() if exp <= now]
                if not expired:
                    continue
                for plate in expired:
                    del lot.reservations[plate]
                    log.info(
                        json.dumps(
                            {
                                "type": "reservation_expired",
                                "lotId": lot.lotId,
                                "plate": plate,
                                "ts": int(time.time() * 1000),
                            }
                        )
                    )
                freeAfter = self.freeCountLocked(lot)
                if freeAfter != freeBefore:
                    changed.append((lot.lotId, freeAfter))
        return changed
