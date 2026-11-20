# VideoStudio validation

The Windows integration baseline was established with VideoStudio 0.1.0, Python 3.11, CyToolsCore 0.12.0, CyUI 1.2.0 and an NVIDIA GeForce RTX 3090. The 0.1.1 package includes the AGPL v3 license and the MIT notices for bundled Cy components.

| Check | Evidence |
| --- | --- |
| CyToolsCore | 157 SDK tests passed. |
| VideoStudio | Nine integration tests cover FFmpeg metadata, preview, packed stereo resize, keyframed motion, all four installed companion entry points, ComfyUI data transfer and targeted cancellation using an HTTP fixture, external model path mapping and application rollback. |
| Native MCP | A real MCP stdio client discovered 64 tools, submitted a studio diagnostic job and retrieved the completed job by its ID. |
| ComfyUI | The existing ComfyUI v0.37.0 checkout was launched privately on CPU. A two-node API workflow generated and returned a real 64 × 64 PNG. Finishing the task removed its local artifact. The real server listed a checkpoint referenced from an external directory. |
| CyVR GPU | Separate RTX Video Super Resolution and DLSS SR jobs completed on a synthetic input and each exported 320 × 192 ProRes. |
| Stereo CUDA | VideoToStereo3D converted 12 frames to 160 × 96 half SBS on CUDA device 0. The engine reported 2,784,190,976 peak VRAM bytes. |
| Companion media | MiniMax H3 frame extraction, Gaussian video analysis, stereo packaging and CyVR CPU resize used the existing companion interpreters. |
| Delivery | Built and launched the Windows executable, exercised English and French desktop screens and rendered a synthetic PLY point cloud in the embedded viewport. The release ZIP includes native Core and CyUI wheels. The ZIP has no weights, drivers, CUDA Toolkit or PyTorch. |

The short clips exercise the integration and output contracts. Long-form throughput, stereo perception quality and production memory use still need evaluation. MiniMax H3 generation on its licensed bridge, 4D/SHARP/C4G inference, DLSS 5 NR, ReShade, downloads from gated model publishers and execution on Linux and macOS remain to be tested. See the [technical guide](README.md#operations-and-validation).
