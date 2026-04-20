"""
test_vport.py — smoke test for VirtualPort

Verifies that data written through VirtualPort arrives correctly when read
back via pyserial's socket:// URL handler.

Run:
    python test_vport.py
"""
import sys
import threading
import time

import serial

from emulator.virtual_port import VirtualPort

# Lines the emulator will send
TEST_LINES = [
    b"T=25.4 H=60.1\r\n",
    b"T=25.5 H=60.0\r\n",
    b"FAULT: overheat\r\n",
]


def emulator_thread(vp: VirtualPort) -> None:
    """Feeds test lines into the virtual port with small delays."""
    if not vp.wait_for_client(timeout=5.0):
        print("[emulator] ERROR: client never connected")
        return
    for line in TEST_LINES:
        vp.write(line)
        time.sleep(0.05)


def main() -> None:
    print("Opening virtual port...")
    vp = VirtualPort()
    url = vp.open()
    print(f"  Server URL : {url}")

    # Start emulator in background — it waits for the reader to connect first
    t = threading.Thread(target=emulator_thread, args=(vp,), daemon=True)
    t.start()

    # Open reader side via pyserial socket:// handler
    print("Connecting reader...")
    try:
        reader = serial.serial_for_url(url, baudrate=115200, timeout=2.0)
    except Exception as e:
        print(f"ERROR opening reader: {e}")
        vp.close()
        sys.exit(1)

    print("Reading lines:")
    received = []
    for _ in range(len(TEST_LINES)):
        line = reader.readline()
        received.append(line)
        print(f"  {line!r}")

    reader.close()
    vp.close()
    t.join(timeout=2.0)

    # Verify
    failed = False
    for expected, actual in zip(TEST_LINES, received):
        if actual != expected:
            print(f"FAIL  expected {expected!r}, got {actual!r}")
            failed = True

    if failed:
        print("\nResult: FAIL")
        sys.exit(1)
    else:
        print("\nResult: OK — all lines match")


if __name__ == "__main__":
    main()
