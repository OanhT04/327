#### Test
      On one terminal:
        python server.py --config config.json
      2nd terminal: test subscribing to lot A and will catch all the event changes pertaining to lot A when test.py is ran
        python clientSubTest.py
      3rd terminal: reserves lot space and other requests;
        python Test.py

## Requirements 
## A. Multithreaded Parking Server Chapter 3 
-> Completed TCP commands work
## B Minimal RPC layer with Request and Reply 
-> With test file it works fine, but a client stub is needed
## C Asynchronous: Update works fine,
Subscription logic needed: I am not too sure about my implementation for pub/sub so if it is not correct w what he wants or has issues feel free to change it


