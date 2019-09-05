"""LCM type definitions
This file automatically generated by lcm.
DO NOT MODIFY BY HAND!!!!
"""

try:
    import cStringIO.StringIO as BytesIO
except ImportError:
    from io import BytesIO
import struct

import npc_control.Waypoint

class connect_request(object):
    __slots__ = ["init_pos"]

    __typenames__ = ["npc_control.Waypoint"]

    __dimensions__ = [None]

    def __init__(self):
        self.init_pos = npc_control.Waypoint()

    def encode(self):
        buf = BytesIO()
        buf.write(connect_request._get_packed_fingerprint())
        self._encode_one(buf)
        return buf.getvalue()

    def _encode_one(self, buf):
        assert self.init_pos._get_packed_fingerprint() == npc_control.Waypoint._get_packed_fingerprint()
        self.init_pos._encode_one(buf)

    def decode(data):
        if hasattr(data, 'read'):
            buf = data
        else:
            buf = BytesIO(data)
        if buf.read(8) != connect_request._get_packed_fingerprint():
            raise ValueError("Decode error")
        return connect_request._decode_one(buf)
    decode = staticmethod(decode)

    def _decode_one(buf):
        self = connect_request()
        self.init_pos = npc_control.Waypoint._decode_one(buf)
        return self
    _decode_one = staticmethod(_decode_one)

    _hash = None
    def _get_hash_recursive(parents):
        if connect_request in parents: return 0
        newparents = parents + [connect_request]
        tmphash = (0x6e6998c81d5f83d2+ npc_control.Waypoint._get_hash_recursive(newparents)) & 0xffffffffffffffff
        tmphash  = (((tmphash<<1)&0xffffffffffffffff) + (tmphash>>63)) & 0xffffffffffffffff
        return tmphash
    _get_hash_recursive = staticmethod(_get_hash_recursive)
    _packed_fingerprint = None

    def _get_packed_fingerprint():
        if connect_request._packed_fingerprint is None:
            connect_request._packed_fingerprint = struct.pack(">Q", connect_request._get_hash_recursive([]))
        return connect_request._packed_fingerprint
    _get_packed_fingerprint = staticmethod(_get_packed_fingerprint)

