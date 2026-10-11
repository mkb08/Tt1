"""Sports prediction markets: Polymarket and Kalshi.

- `whales`: watch both venues for big trades on game markets.
- `stink`:  rest deep-discount limit bids ("stink bids") on game winners and
            wait for a seller in a hurry; optionally hedge on the other venue.

Public data needs no keys and only the standard library. Live orders need
`cryptography` (Kalshi) and `polymarket-client` (Polymarket).
"""
