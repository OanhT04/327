# state.py
import json # Importing json module to handle JSON file
import logging # Importing logging module for tracking server events
import threading # Importing threading module to do thread synchronization
import time # Importing time module to use timestamps

log = logging.getLogger("parking")

# ---- Data model for one lot ----
class LotState:
    def __init__(self, lotId, capacity: int): # Initializing a new lot
        self.lotId = lotId
        self.capacity = int(capacity)
        self.occupiedPhysical = 0
        # plate -> expires_at (unix seconds)
        self.reservations = {} # Initializing a dictionary that will map license plates to expiry timestamps


class ParkingState:
    """In-memory parking state.

    Thread-safety:- All mutations and reads that depend on multiple fields are protected by self.lock.
    """

    def __init__(self, lots, reservationTtlSec: int): # Creating the global state
        # lock protects all shared state changes (reserve/cancel/sensor/expire)
        self.lock = threading.RLock() # Initializing the lock for thread safety
        self.ttl = int(reservationTtlSec)
        self.lots = {}
        for lotId, cap in lots.items(): # Iterating through provided configuration
            self.lots[lotId] = LotState(lotId, int(cap)) # Creating each lot state and converting capacity to an integer

    def freeCountLocked(self, lot: LotState) -> int: # This function will calculate available spots
        return lot.capacity - lot.occupiedPhysical - len(lot.reservations)

    def allLots(self): # This function will get a summary of all lots for the UI/API
        with self.lock: # Acquiring lock before reading
            result = [] # Initializing a list to hold the summary data
            for lot in self.lots.values(): # Looping through every lot
                free = self.freeCountLocked(lot) # Calculating the current free spots
                occupiedTotal = lot.occupiedPhysical + len(lot.reservations)
                result.append( # Appending the list with lot details to result
                    {"id": lot.lotId, "capacity": lot.capacity, "occupied": occupiedTotal, "free": free}
                )
            return result # Returning the complete list

    def availability(self, lotId: str) -> int: # This function will check for free spots of one specific lot
        with self.lock: # Ensuring thread-safe read
            if lotId not in self.lots: # Verifying if lot exists, return error in invalid
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            return self.freeCountLocked(lot) # Returning the calculated free count

    def reserve(self, lotId: str, plate: str):
        """Attempt to reserve a spot for plate in lotId.

        Returns: (ok:bool, status:str, changed_free:bool)
        status in {OK, FULL, EXISTS}
        """
        with self.lock:
            if lotId not in self.lots: # Validating lot, if not return error
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
            return True, "OK", freeAfter != freeBefore # Return success and if count changed

    def cancel(self, lotId: str, plate: str): # This function is use to remove a reservation manually
        """Cancel an existing reservation.

        Returns: (ok:bool, status:str, changed_free:bool)
        status in {OK, NOT_FOUND}
        """
        with self.lock:
            if lotId not in self.lots: # Validating lot, if not return error
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            freeBefore = self.freeCountLocked(lot) # Storing state before deletion

            if plate not in lot.reservations: # Checking if reservation exists, if it doesn't exist returns an error message
                return False, "NOT_FOUND", False

            del lot.reservations[plate] # Removing entry from dictionary
            freeAfter = self.freeCountLocked(lot) # Calculating new availability

            log.info( # Logging the cancellation
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
            if lotId not in self.lots: # Validating lot
                raise KeyError("unknown lot")
            lot = self.lots[lotId]
            freeBefore = self.freeCountLocked(lot) # Returning success status

            lot.occupiedPhysical += int(delta)
            if lot.occupiedPhysical < 0:
                lot.occupiedPhysical = 0

            # Clamp so physical occupancy can't exceed capacity minus active reservations
            maxPhysical = max(0, lot.capacity - len(lot.reservations))
            if lot.occupiedPhysical > maxPhysical:
                lot.occupiedPhysical = maxPhysical

            freeAfter = self.freeCountLocked(lot) # Calculating final availability
            log.info( # Logging sensor activity
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
            return freeAfter, freeAfter != freeBefore # Returning new count and change status

    def expireOnce(self):
        """Expire any reservations past their TTL.

        Returns: list[(lotId, free_after)] for lots whose free count changed.
        """
        now = time.time() # Getting current time for comparison
        changed = [] # Tracking which lots changed due to expiry
        with self.lock:
            for lot in self.lots.values(): # Iterating through every lot
                freeBefore = self.freeCountLocked(lot)
                expired = [p for p, exp in lot.reservations.items() if exp <= now]
                if not expired: # Skiping if no expiries
                    continue
                for plate in expired:
                    del lot.reservations[plate] # Removing from dictionary
                    log.info( # Logging the automatic expiry
                        json.dumps(
                            {
                                "type": "reservation_expired",
                                "lotId": lot.lotId,
                                "plate": plate,
                                "ts": int(time.time() * 1000),
                            }
                        )
                    )
                freeAfter = self.freeCountLocked(lot) # Recalculating after cleanup
                if freeAfter != freeBefore: # If capacity was freed, then add to the report list 
                    changed.append((lot.lotId, freeAfter))
        return changed # Returning the list of affected lots
