"""isodaq-agent: serve a machine's serial ports to IsoDAQ over the network (AGT1).

Run ``python -m isodaq_agent serve`` on the machine the boards are plugged into
(a Raspberry Pi by a test rack), then ``python -m isodaq_agent pair`` to get a
one-time code for IsoDAQ. Needs pyserial and cryptography; no Qt.
"""
__version__ = "0.1.0"
