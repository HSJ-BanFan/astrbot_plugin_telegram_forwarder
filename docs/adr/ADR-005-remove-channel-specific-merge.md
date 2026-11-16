# ADR-005: Remove the channel-specific merge strategy

The distributed plugin offers generic keyword-based merging. Remove the
hardcoded SomeACG implementation, registry entry, schema preset and frontend
choice because that strategy belongs to one channel rather than every user's
installation. Removing only the preset would leave the same private behavior
available in the editor and in the runtime registry.

Filter the retired class at Web configuration read and save boundaries. Reading
configuration does not write to a device's saved configuration. The next
explicit save omits the retired entry while retaining other rules and unknown
extension parameters. The runtime treats the retired class as unsupported.

Keep non-adjacent group ownership tests using a synthetic association rule so
removing this strategy does not remove coverage of the merger's shared contract.
