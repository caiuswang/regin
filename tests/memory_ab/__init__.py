"""regin-vs-Hindsight memory A/B harness.

A shared corpus + probe set, two system adapters, and one system-agnostic
scorer. Both systems ingest the *identical* corpus (byte-for-byte bodies);
recall, capture round-trip, and lifecycle are scored from a common result
dump shape so the scorecard compares like with like. See README.md.
"""
