# Third-party notices

- **TotK Recomp** by Valiantsin Dzerakh, MIT, pinned at `7a37fc3c346cd6f2b3c80637effb27b880ce817b`. The runtime overlay contains modified upstream sources. Its copyright and upstream attributions remain in [UPSTREAM-MIT.txt](licenses/UPSTREAM-MIT.txt) and [UPSTREAM-NOTICES.md](licenses/UPSTREAM-NOTICES.md).
- **NVIDIA Streamline 2.14.16** header sources under `src/runtime-overlay/third_party/streamline/` retain their NVIDIA MIT notices. The local headers that adapt the interface are part of the overlay. The Streamline and DLSS DLLs are supplied separately by their vendor.
- **AMD FidelityFX** API headers under `src/runtime-overlay/third_party/fidelityfx*` retain their AMD MIT notices. This notice covers the source headers. SDK loader and frame-generation effect DLLs have separate distribution terms.
- **Stellarznith, Stavaas and Alerion** are the names of third-party PS5 icon styles supported by the manager. The public repository contains the selector and loader code. The icon packages, underlying game layout archives, font files and adapted guide art are outside the public tree.
- The manager uses **.NET Framework** and **Python 3.14** locally. Upstream may use additional libraries when building the game runtime. Its notices describe the linked dependencies.

The root MIT license applies to original project contributions. Each third-party work keeps its own license and copyright.
