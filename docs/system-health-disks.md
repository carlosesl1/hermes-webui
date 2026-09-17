# System health disks

The authenticated Insights panel shows each local mounted disk filesystem visible
to the WebUI process, with device, mount point, used/total capacity and free space.
Linux bind aliases are deduplicated by major:minor device identity; ephemeral
filesystems, Docker overlays and loop images are excluded. Multiple partitions
on a physical disk remain separate because their capacities are independent.

In Docker this is the mounted storage visible to the container, not an inventory
of unmounted drives or inaccessible host partitions. Bind the desired host storage
into the container to monitor it. No Docker socket or privileged host command is
used by this collector. Network filesystems are not probed. Platforms without
Linux mountinfo retain the root-disk fallback.

The legacy `disk` API object is unchanged. The additive `disks` list intentionally
exposes device and mount labels behind the existing authentication guard; no mount
options, process metadata, credentials or exception messages are returned. A failed
volume is marked unavailable, never reported as an empty disk.
