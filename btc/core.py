import btc.config
import btc.ripemd160
import btc.secp256k1
import hashlib
import math
import io
import json
import typing


sighash_all = 0x01
sighash_none = 0x02
sighash_single = 0x03
sighash_anyone_can_pay = 0x80


def hash160(data: bytearray) -> bytearray:
    return bytearray(btc.ripemd160.ripemd160(hashlib.sha256(data).digest()).digest())


def hash256(data: bytearray) -> bytearray:
    return bytearray(hashlib.sha256(hashlib.sha256(data).digest()).digest())


class PriKey:
    def __init__(self, n: int):
        self.n = n

    def __repr__(self):
        return json.dumps(self.json())

    def __eq__(self, other):
        return self.n == other.n

    def json(self):
        return f'0x{self.n:064x}'

    def pubkey(self):
        pubkey = btc.secp256k1.G * btc.secp256k1.Fr(self.n)
        return PubKey(pubkey.x.x, pubkey.y.x)

    def sign(self, data: bytearray):
        assert len(data) == 32
        m = btc.secp256k1.Fr(int.from_bytes(data))
        r, s, v = btc.ecdsa.sign(btc.secp256k1.Fr(self.n), m)
        for _ in range(8):
            r, s, v = btc.ecdsa.sign(btc.secp256k1.Fr(self.n), m)
            if v > 1:
                continue
            # We require that the S value inside ECDSA signatures is at most the curve order divided by 2 (essentially
            # restricting this value to its lower half range).
            # See: https://github.com/bitcoin/bips/blob/master/bip-0146.mediawiki
            if s.x * 2 >= btc.secp256k1.N:
                s = -s
                v = 1 - v
            return r, s, v
        raise Exception

    def wif(self):
        # See https://en.bitcoin.it/wiki/Wallet_import_format
        data = bytearray()
        data.append(btc.config.current.prefix.wif)
        data.extend(self.n.to_bytes(32))
        data.append(0x01)
        checksum = hash256(data)[:4]
        data.extend(checksum)
        return btc.base58.encode(data)

    @staticmethod
    def wif_read(data: str):
        data = btc.base58.decode(data)
        assert data[0] == btc.config.current.prefix.wif
        assert hash256(data[:-4])[:4] == data[-4:]
        return PriKey(int.from_bytes(data[1:33]))


class PubKey:
    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y

    def __repr__(self):
        return json.dumps(self.json())

    def __eq__(self, other):
        return all([
            self.x == other.x,
            self.y == other.y,
        ])

    def json(self):
        return {
            'x': f'0x{self.x:064x}',
            'y': f'0x{self.y:064x}'
        }

    def sec(self):
        r = bytearray()
        if self.y & 1 == 0:
            r.append(0x02)
        else:
            r.append(0x03)
        r.extend(self.x.to_bytes(32))
        return r

    @staticmethod
    def sec_read(data: bytearray):
        p = data[0]
        assert p in [0x02, 0x03, 0x04]
        x = int.from_bytes(data[1:33])
        if p == 0x04:
            y = int.from_bytes(data[33:65])
        else:
            y_x_y = x * x * x + btc.secp256k1.A.x * x + btc.secp256k1.B.x
            y = pow(y_x_y, (btc.secp256k1.P + 1) // 4, btc.secp256k1.P)
            if y & 1 != p - 2:
                y = -y % btc.secp256k1.P
        return PubKey(x, y)

# Bitcoin address prefix: https://en.bitcoin.it/wiki/List_of_address_prefixes


def address_p2pkh(pubkey: PubKey) -> str:
    # Legacy
    pubkey_hash = hash160(pubkey.sec())
    version = bytearray([btc.config.current.prefix.p2pkh])
    checksum = hash256(version + pubkey_hash)
    address = btc.base58.encode(version + pubkey_hash + checksum[:4])
    return address


def address_p2sh(pubkey: PubKey) -> str:
    # Nested Segwit.
    # See https://github.com/bitcoin/bips/blob/master/bip-0141.mediawiki
    pubkey_hash = hash160(pubkey.sec())
    redeem_hash = hash160(bytearray([0x00, 0x14]) + pubkey_hash)
    version = bytearray([btc.config.current.prefix.p2sh])
    checksum = hash256(version + redeem_hash)
    address = btc.base58.encode(version + redeem_hash + checksum[:4])
    return address


def address_p2wpkh(pubkey: PubKey) -> str:
    # Native SegWit.
    # See https://github.com/bitcoin/bips/blob/master/bip-0084.mediawiki
    pubkey_hash = hash160(pubkey.sec())
    return btc.bech32.encode(btc.config.current.prefix.bech32, 0, pubkey_hash)


def address_p2tr(pubkey: PubKey) -> str:
    # Taproot.
    # See https://github.com/bitcoin/bips/blob/master/bip-0341.mediawiki
    if pubkey.y & 1 != 0:
        # Taproot requires that the y coordinate of the public key is even.
        pubkey.y = btc.secp256k1.P - pubkey.y
    tweak_k_data = bytearray([
        0xe8, 0x0f, 0xe1, 0x63, 0x9c, 0x9c, 0xa0, 0x50, 0xe3, 0xaf, 0x1b, 0x39, 0xc1, 0x43, 0xc6, 0x3e,
        0x42, 0x9c, 0xbc, 0xeb, 0x15, 0xd9, 0x40, 0xfb, 0xb5, 0xc5, 0xa1, 0xf4, 0xaf, 0x57, 0xc5, 0xe9,
        0xe8, 0x0f, 0xe1, 0x63, 0x9c, 0x9c, 0xa0, 0x50, 0xe3, 0xaf, 0x1b, 0x39, 0xc1, 0x43, 0xc6, 0x3e,
        0x42, 0x9c, 0xbc, 0xeb, 0x15, 0xd9, 0x40, 0xfb, 0xb5, 0xc5, 0xa1, 0xf4, 0xaf, 0x57, 0xc5, 0xe9
    ])
    tweak_k_data.extend(pubkey.sec()[1:])
    tweak_k = btc.secp256k1.Fr(int.from_bytes(hashlib.sha256(tweak_k_data).digest()))
    tweak_p = btc.secp256k1.G * tweak_k
    tweak_p = btc.secp256k1.Pt(btc.secp256k1.Fq(pubkey.x), btc.secp256k1.Fq(pubkey.y)) + tweak_p
    return btc.bech32m.encode(btc.config.current.prefix.bech32, 1, bytearray(tweak_p.x.x.to_bytes(32)))


def compact_size_encode(n: int) -> bytearray:
    # Integer can be encoded depending on the represented value to save space. Variable length integers always precede
    # an array/vector of a type of data that may vary in length. Longer numbers are encoded in little endian.
    # See: https://en.bitcoin.it/wiki/Protocol_documentation#Variable_length_integer
    assert n >= 0
    assert n <= 0xffffffffffffffff
    if n <= 0xfc:
        return bytearray([n])
    if n <= 0xffff:
        return bytearray([0xfd]) + bytearray(n.to_bytes(2, 'little'))
    if n <= 0xffffffff:
        return bytearray([0xfe]) + bytearray(n.to_bytes(4, 'little'))
    if n <= 0xffffffffffffffff:
        return bytearray([0xff]) + bytearray(n.to_bytes(8, 'little'))
    raise Exception


def compact_size_decode(data: bytearray) -> int:
    head = data[0]
    if head <= 0xfc:
        return head
    if head == 0xfd:
        assert len(data) == 3
    if head == 0xfe:
        assert len(data) == 5
    if head == 0xff:
        assert len(data) == 9
    return int.from_bytes(data[1:], 'little')


def compact_size_decode_reader(reader: typing.BinaryIO) -> int:
    head = reader.read(1)[0]
    if head <= 0xfc:
        return head
    if head == 0xfd:
        return int.from_bytes(reader.read(2), 'little')
    if head == 0xfe:
        return int.from_bytes(reader.read(4), 'little')
    if head == 0xff:
        return int.from_bytes(reader.read(8), 'little')
    raise Exception


class OutPoint:
    def __init__(self, txid: bytearray, vout: int):
        assert len(txid) == 32
        assert vout >= 0
        assert vout <= 0xffffffff
        self.txid = txid
        self.vout = vout

    def __repr__(self):
        return json.dumps(self.json())

    def __eq__(self, other):
        return all([
            self.txid == other.txid,
            self.vout == other.vout,
        ])

    def copy(self):
        return OutPoint(self.txid.copy(), self.vout)

    def json(self):
        return {
            'txid': f'0x{self.txid.hex()}',
            'vout': self.vout,
        }


class TxIn:
    def __init__(self, out_point: OutPoint, script_sig: bytearray, sequence: int, witness: bytearray):
        assert sequence >= 0
        assert sequence <= 0xffffffff
        self.out_point = out_point
        self.script_sig = script_sig
        self.sequence = sequence
        self.witness = witness

    def __repr__(self):
        return json.dumps(self.json())

    def __eq__(self, other):
        return all([
            self.out_point == other.out_point,
            self.script_sig == other.script_sig,
            self.sequence == other.sequence,
            self.witness == other.witness,
        ])

    def copy(self):
        return TxIn(self.out_point.copy(), self.script_sig.copy(), self.sequence, self.witness.copy())

    def json(self):
        return {
            'out_point': self.out_point.json(),
            'script_sig': f'0x{self.script_sig.hex()}',
            'sequence': self.sequence,
            'witness': f'0x{self.witness.hex()}',
        }


class TxOut:
    def __init__(self, value: int, script_pubkey: bytearray):
        assert value >= 0
        assert value <= 0xffffffffffffffff
        self.value = value
        self.script_pubkey = script_pubkey

    def __repr__(self):
        return json.dumps(self.json())

    def __eq__(self, other):
        return all([
            self.value == other.value,
            self.script_pubkey == other.script_pubkey,
        ])

    def copy(self):
        return TxOut(self.value, self.script_pubkey.copy())

    def json(self):
        return {
            'value': self.value,
            'script_pubkey': f'0x{self.script_pubkey.hex()}',
        }


class Transaction:
    # Referring to the design of Bitcoin core.
    # See: https://github.com/bitcoin/bitcoin/blob/master/src/primitives/transaction.h
    def __init__(self, version: int, vin: typing.List[TxIn], vout: typing.List[TxOut], locktime: int):
        self.version = version
        self.vin = vin
        self.vout = vout
        self.locktime = locktime

    def __repr__(self):
        return json.dumps(self.json())

    def __eq__(self, other):
        return all([
            self.version == other.version,
            self.vin == other.vin,
            self.vout == other.vout,
            self.locktime == other.locktime,
        ])

    def copy(self):
        return Transaction(self.version, [i.copy() for i in self.vin], [o.copy() for o in self.vout], self.locktime)

    def digest_legacy(self, i: int, sighash: int):
        # The legacy signing algorithm is used to create signatures that will unlock non-segwit locking scripts.
        # See: https://learnmeabitcoin.com/technical/keys/signature/
        tx = self.copy()
        for e in tx.vin:
            e.script_sig = bytearray()
        # Put the script_pubkey as a placeholder in the script_sig.
        tx_out_result = btc.rpc.get_tx_out(tx.vin[i].out_point.txid[::-1].hex(), tx.vin[i].out_point.vout)
        script_pubkey = bytearray.fromhex(tx_out_result['scriptPubKey']['hex'])
        tx.vin[i].script_sig = script_pubkey
        if sighash & sighash_anyone_can_pay:
            tx.vin = [tx.vin[i]]
        if sighash & 0x1f == sighash_all:
            pass
        if sighash & 0x1f == sighash_none:
            tx.vout = []
        if sighash & 0x1f == sighash_single:
            tx.vout = [tx.vout[i]]
        data = tx.serialize_legacy()
        # Append signature hash type to transaction data. The most common is SIGHASH_ALL (0x01), which indicates that
        # the signature covers all of the inputs and outputs in the transaction. This means that nobody else can add
        # any additional inputs or outputs to it later on.
        # The sighash when appended to the transaction data is 4 bytes and in little-endian byte order.
        data.extend(bytearray([sighash, 0x00, 0x00, 0x00]))
        return hash256(data)

    def digest_segwit(self, i: int, sighash: int):
        # See: https://github.com/bitcoin/bips/blob/master/bip-0143.mediawiki
        data = bytearray()
        # 1. nVersion of the transaction (4-byte little endian)
        data.extend(self.version.to_bytes(4, 'little'))
        # 2. hashPrevouts (32-byte hash)
        # If the ANYONECANPAY flag is not set, hashPrevouts is the double SHA256 of the serialization of all input
        # outpoints; Otherwise, hashPrevouts is a uint256 of 0x0000......0000.
        hash_prevouts = bytearray(32)
        if sighash & sighash_anyone_can_pay == 0x00:
            snap = bytearray()
            for j in self.vin:
                snap.extend(j.out_point.txid)
                snap.extend(j.out_point.vout.to_bytes(4, 'little'))
            hash_prevouts = hash256(snap)
        data.extend(hash_prevouts)
        # 3. hashSequence (32-byte hash)
        hash_sequence = bytearray(32)
        if sighash == sighash_all:
            snap = bytearray()
            for j in self.vin:
                snap.extend(j.sequence.to_bytes(4, 'little'))
            hash_sequence = hash256(snap)
        data.extend(hash_sequence)
        # 4. outpoint (32-byte hash + 4-byte little endian)
        data.extend(self.vin[i].out_point.txid)
        data.extend(self.vin[i].out_point.vout.to_bytes(4, 'little'))
        # 5. scriptCode of the input (serialized as scripts inside CTxOuts)
        tx_out_result = btc.rpc.get_tx_out(self.vin[i].out_point.txid[::-1].hex(), self.vin[i].out_point.vout)
        script_pubkey = bytearray.fromhex(tx_out_result['scriptPubKey']['hex'])
        assert script_pubkey[0] == 0x00
        assert script_pubkey[1] == 0x14
        pubkey_hash = script_pubkey[0x02:0x16]
        assert len(pubkey_hash) == 20
        script_code = bytearray([0x19, 0x76, 0xa9, 0x14]) + pubkey_hash + bytearray([0x88, 0xac])
        data.extend(script_code)
        # 6. value of the output spent by this input (8-byte little endian)
        value = tx_out_result['value'] * btc.denomination.bitcoin
        value = int(value.to_integral_exact())
        data.extend(value.to_bytes(8, 'little'))
        # 7. nSequence of the input (4-byte little endian)
        data.extend(self.vin[i].sequence.to_bytes(4, 'little'))
        # 8. hashOutputs (32-byte hash)
        hash_outputs = bytearray(32)
        if sighash & 0x1f == sighash_all:
            snap = bytearray()
            for u in self.vout:
                snap.extend(u.value.to_bytes(8, 'little'))
                snap.extend(compact_size_encode(len(u.script_pubkey)))
                snap.extend(u.script_pubkey)
            hash_outputs = hash256(snap)
        if sighash & 0x1f == sighash_single and i < len(self.vout):
            raise Exception
            snap = bytearray()
            u = self.vout[i]
            snap.extend(u.value.to_bytes(8, 'little'))
            snap.extend(compact_size_encode(len(u.script_pubkey)))
            snap.extend(u.script_pubkey)
            hash_outputs = hash256(snap)
        data.extend(hash_outputs)
        # 9. nLocktime of the transaction (4-byte little endian)
        data.extend(self.locktime.to_bytes(4, 'little'))
        # 10. sighash type of the signature (4-byte little endian)
        data.extend(bytearray([sighash, 0x00, 0x00, 0x00]))
        return hash256(data)

    def json(self):
        return {
            'version': self.version,
            'vin': [e.json() for e in self.vin],
            'vout': [e.json() for e in self.vout],
            'locktime': self.locktime,
        }

    def serialize_legacy(self):
        data = bytearray()
        data.extend(self.version.to_bytes(4, 'little'))
        data.extend(compact_size_encode(len(self.vin)))
        for i in self.vin:
            data.extend(i.out_point.txid)
            data.extend(i.out_point.vout.to_bytes(4, 'little'))
            data.extend(compact_size_encode(len(i.script_sig)))
            data.extend(i.script_sig)
            data.extend(i.sequence.to_bytes(4, 'little'))
        data.extend(compact_size_encode(len(self.vout)))
        for o in self.vout:
            data.extend(o.value.to_bytes(8, 'little'))
            data.extend(compact_size_encode(len(o.script_pubkey)))
            data.extend(o.script_pubkey)
        data.extend(self.locktime.to_bytes(4, 'little'))
        return data

    def serialize_segwit(self):
        data = bytearray()
        data.extend(self.version.to_bytes(4, 'little'))
        data.append(0x00)
        data.append(0x01)
        data.extend(compact_size_encode(len(self.vin)))
        for i in self.vin:
            data.extend(i.out_point.txid)
            data.extend(i.out_point.vout.to_bytes(4, 'little'))
            data.extend(compact_size_encode(len(i.script_sig)))
            data.extend(i.script_sig)
            data.extend(i.sequence.to_bytes(4, 'little'))
        data.extend(compact_size_encode(len(self.vout)))
        for o in self.vout:
            data.extend(o.value.to_bytes(8, 'little'))
            data.extend(compact_size_encode(len(o.script_pubkey)))
            data.extend(o.script_pubkey)
        for i in self.vin:
            data.extend(i.witness if i.witness else bytearray([0x00]))
        data.extend(self.locktime.to_bytes(4, 'little'))
        return data

    def serialize(self):
        # If any inputs have nonempty witnesses, the entire transaction is serialized in the BIP141 Segwit format which
        # includes a list of witnesses.
        # See: https://github.com/bitcoin/bips/blob/master/bip-0141.mediawiki
        if any([e.witness != bytearray() for e in self.vin]):
            return self.serialize_segwit()
        else:
            return self.serialize_legacy()

    @staticmethod
    def serialize_read_legacy(data: bytearray):
        reader = io.BytesIO(data)
        tx = Transaction(0, [], [], 0)
        tx.version = int.from_bytes(reader.read(4), 'little')
        for _ in range(compact_size_decode_reader(reader)):
            txid = bytearray(reader.read(32))
            vout = int.from_bytes(reader.read(4), 'little')
            script_sig = bytearray(reader.read(compact_size_decode_reader(reader)))
            sequence = int.from_bytes(reader.read(4), 'little')
            tx.vin.append(TxIn(OutPoint(txid, vout), script_sig, sequence, bytearray()))
        for _ in range(compact_size_decode_reader(reader)):
            value = int.from_bytes(reader.read(8), 'little')
            script_pubkey = bytearray(reader.read(compact_size_decode_reader(reader)))
            tx.vout.append(TxOut(value, script_pubkey))
        tx.locktime = int.from_bytes(reader.read(4), 'little')
        return tx

    @staticmethod
    def serialize_read_segwit(data: bytearray):
        reader = io.BytesIO(data)
        tx = Transaction(0, [], [], 0)
        tx.version = int.from_bytes(reader.read(4), 'little')
        assert reader.read(1)[0] == 0x00
        assert reader.read(1)[0] == 0x01
        for _ in range(compact_size_decode_reader(reader)):
            txid = bytearray(reader.read(32))
            vout = int.from_bytes(reader.read(4), 'little')
            script_sig = bytearray(reader.read(compact_size_decode_reader(reader)))
            sequence = int.from_bytes(reader.read(4), 'little')
            tx.vin.append(TxIn(OutPoint(txid, vout), script_sig, sequence, bytearray()))
        for _ in range(compact_size_decode_reader(reader)):
            value = int.from_bytes(reader.read(8), 'little')
            script_pubkey = bytearray(reader.read(compact_size_decode_reader(reader)))
            tx.vout.append(TxOut(value, script_pubkey))
        for i in range(compact_size_decode_reader(reader)):
            witness = bytearray(reader.read(compact_size_decode_reader(reader)))
            tx.vin[i].witness = witness
        tx.locktime = int.from_bytes(reader.read(4), 'little')
        return tx

    @staticmethod
    def serialize_read(data: bytearray):
        if data[4] == 0x00:
            return Transaction.serialize_read_segwit(data)
        else:
            return Transaction.serialize_read_legacy(data)

    def txid(self):
        return hash256(self.serialize_legacy())

    def vbytes(self):
        return math.ceil(self.weight() / 4.0)

    def weight(self):
        data = self.serialize()
        size_segwit = 0
        if data[4] == 0x00 and data[5] == 0x01:
            size_segwit += 2
            for i in self.vin:
                size_segwit += len(i.witness) if i.witness else 1
        size_legacy = len(data) - size_segwit
        return size_legacy * 4 + size_segwit * 1


def script_pubkey_p2pkh(addr: str) -> bytearray:
    data = btc.base58.decode(addr)
    assert data[0] == btc.config.current.prefix.p2pkh
    hash = data[0x01:0x15]
    assert btc.core.hash256(data[0x00:0x15])[:4] == data[0x15:0x19]
    return bytearray([0x76, 0xa9, 0x14]) + hash + bytearray([0x88, 0xac])


def script_pubkey_p2sh(addr: str) -> bytearray:
    data = btc.base58.decode(addr)
    assert data[0] == btc.config.current.prefix.p2sh
    hash = data[0x01:0x15]
    assert btc.core.hash256(data[0x00:0x15])[:4] == data[0x15:0x19]
    return bytearray([0xa9, 0x14]) + hash + bytearray([0x87])


def script_pubkey_p2wpkh(addr: str) -> bytearray:
    _, hash = btc.bech32.decode(btc.config.current.prefix.bech32, addr)
    return bytearray([0x00, 0x14]) + hash


def der_encode(r: btc.secp256k1.Fr, s: btc.secp256k1.Fr) -> bytearray:
    # DER encoding: https://github.com/bitcoin/bips/blob/master/bip-0062.mediawiki#der-encoding
    body = bytearray()
    body.append(0x02)
    r = r.x.to_bytes(32).lstrip(bytearray([0x00]))
    if r[0] & 0x80:
        r = bytearray([0x00]) + r
    body.append(len(r))
    body.extend(r)
    body.append(0x02)
    s = s.x.to_bytes(32).lstrip(bytearray([0x00]))
    if s[0] & 0x80:
        s = bytearray([0x00]) + s
    body.append(len(s))
    body.extend(s)
    head = bytearray([0x30, len(body)])
    return head + body


def der_decode(sign: bytearray) -> typing.Tuple[btc.secp256k1.Fr, btc.secp256k1.Fr]:
    assert sign[0] == 0x30
    assert sign[1] == len(sign) - 2
    assert sign[2] == 0x02
    rlen = sign[3]
    r = btc.secp256k1.Fr(int.from_bytes(sign[4:4+rlen]))
    f = 4 + rlen
    assert sign[f] == 0x02
    slen = sign[f+1]
    f = f + 2
    s = btc.secp256k1.Fr(int.from_bytes(sign[f:f+slen]))
    return r, s
