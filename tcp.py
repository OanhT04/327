# tcp.py
import json # Importing json module to handle JSON file

def textClient(server, conn, addr):
    conn.settimeout(60) # Setting a 60-second idle timeout for the connection
    f = conn.makefile("rwb") # Creating wrapper to read and write bytes
    while not server.stopEvent.is_set(): # This loop will run until there's a signal to stop
        line = f.readline() # Reading a single line of text from the sensor
        if not line: # If readline returns empty, the sensor disconnected, then terminate
            return
        line = line.decode("utf-8", errors="replace").strip() # Converting the bytes to string and remove whitespace
        if not line: # If line is empty, then skip
            continue

        parts = line.split() # Splitting the command into components
        cmd = parts[0].upper() # Extracting the command name and making it to uppercase

        try:
            if cmd == "PING":
                f.write(b"PONG\n") # Responding with PONG to indicate the server is alive

            elif cmd == "LOTS":
                lots = server.state.allLots() # Retrieving summary data for all lots from state
                f.write((json.dumps(lots) + "\n").encode("utf-8")) # Sending encoded JSON list to client

            elif cmd == "AVAIL" and len(parts) == 2: # Checking availability for a specific lot
                free = server.state.availability(parts[1]) # Obtaining free count for the requested lot
                f.write((str(free) + "\n").encode("utf-8")) # Sending the count as a string

            elif cmd == "RESERVE" and len(parts) == 3: # Attempting to reserve a spot
                ok, status, changed = server.state.reserve(parts[1], parts[2])
                f.write((status + "\n").encode("utf-8")) # Sending the status
                if changed: # If the reservation successfully changed the free count, then get the updated availability and notify subscribers of the change
                    free = server.state.availability(parts[1])
                    server.pubsub.publish(parts[1], free)

            elif cmd == "CANCEL" and len(parts) == 3: # Attempting to cancel an existing reservation
                ok, status, changed = server.state.cancel(parts[1], parts[2])
                f.write((status + "\n").encode("utf-8")) # Sending the status
                if changed: # If the reservation successfully changed the free count, then get the updated availability and notify subscribers of the change
                    free = server.state.availability(parts[1])
                    server.pubsub.publish(parts[1], free)

            else: # Responding if the command is unrecognized
                f.write(b"ERROR bad_command\n")

            f.flush()# Ensuring all written data is pushed out to the client

        except KeyError:
            f.write(b"ERROR unknown_lot\n")
            f.flush()
        except Exception:
            f.write(b"ERROR server_error\n")
            f.flush()