import queue # Importing queue module to use FIFO queues for buffering
import threading # Importing threading module to do thread synchronization
import time # Importing time module to use timestamps


# The server does not send messages directly from the publisher
# (e.g., reserve(), cancel(), or sensor updates) to clients.
# Instead, it uses message queues:
#
# 1. When availability changes, publish(lotId, free) places an
#    event into a queue.
#
# 2. Each subscriber has its own queue.
#
# 3. The notifier thread (notifierLoop) reads events from queues
#    and sends them to clients.

def nowMs(): # This function will return the current timestamps in milliseconds
    return int(time.time() * 1000) 


class Subscriber:
    def __init__(self, subId, lotId, queueSize):
        self.subId = subId # Placeholder for specific subscription id
        self.lotId = lotId # Placeholder for specific lot id
        self.queue = queue.Queue(maxsize=queueSize) # Queue for user's message
        self.conn = None # Placehilder for socket that will be assign later
        self.connected = False # Flag to check if socket is ready for usage


class PubSub:
    """
    Back-pressure policy (required):
    - bounded per-subscriber queue
    - if queue overflows, disconnect subscriber (drop by disconnect)
    """

    def __init__(self, queueSize=128):
        self.lock = threading.Lock() # Implementing mutex to protect shared state
        self.subscribers = {} # Creating a dictionary that will map subID to subsciber object
        self.nextSubId = 1 # Counter for genertating subID
        self.queueSize = queueSize # Max queue size

    def subscribe(self, lotId):
        with self.lock:
            subId = str(self.nextSubId) # Converting subID counter into a string
            self.nextSubId += 1 # Incrementing the counter by one
            self.subscribers[subId] = Subscriber(subId, lotId, self.queueSize) # Storing new Subscriber object
            return subId # Returning the subID by using RPC

    def unsubscribe(self, subId):
        with self.lock: # Preventing dictionary from being removed
            sub = self.subscribers.pop(subId, None) # If it exists, remove it from the dictionary
        if not sub: # If doesn't, return failure
            return False
        if sub.conn: # If there exist an active socket, close the connection, and ignores any error when closing
            try:
                sub.conn.close()
            except Exception:
                pass
        return True # Returning success

    def attachConnection(self, subId, conn):
        with self.lock: 
            sub = self.subscribers.get(subId) # Getting the subscribed id
            if not sub: # If id is invalid, then refuse connection
                return False
            if sub.conn: # If client already connected somewhere else, close the old connection
                try:
                    sub.conn.close()
                except Exception:
                    pass
            sub.conn = conn # Storing the new socket object
            sub.connected = True # Marking that the loop is ready
            return True

    def publish(self, lotId, free):
        line = f"EVENT {lotId} {free} {nowMs()}\n" # Formatting the event message
        victims = [] # Intializing a list to store subscribers with full queues
        with self.lock:
            for sub in self.subscribers.values(): # Iterating through all subscriber
                if sub.lotId != lotId: # If they are not waiting for the specific lot, then skip
                    continue
                try:
                    sub.queue.put_nowait(line) # Attempting to add the message to the queue without blocking
                except queue.Full: # If the queue is full, mark it for removal
                    victims.append(sub)

        # Back-pressure: disconnect slow subscribers
        for subId in victims:
            sub = self.subscribers.pop(subId, None) # Pooping from an active list
            if not sub:
                continue
            if sub.conn:
                try:
                    sub.conn.close() # Force shutdown the connection
                except Exception:
                    pass
            sub.connected = False # Updating the flag

    def notifierLoop(self, stopEvent):
        while not stopEvent.is_set(): # This loop will run until the server shutdown is triggered
            with self.lock:
                subs = list(self.subscribers.values()) # Copying the subscribers to a list, so it won't be modified by dictionary changes

            for sub in subs: # Iterating through all of the subscribers
                if not sub.connected or not sub.conn: # If there's no active connection, then skip
                    continue
                try:
                    msg = sub.queue.get_nowait() # Obtaining the next message from the queue
                except queue.Empty: # If the queue is empty, go to the next subscriber
                    continue

                try:
                    sub.conn.sendall(msg.encode("utf-8")) # Sending the encoded message to the socket
                except Exception: # If it fails, mark it as disconnected
                    sub.connected = False
            #1ms
            time.sleep(0.001) # Sleep to prevent full CPU usage