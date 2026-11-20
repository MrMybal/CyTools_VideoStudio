# CyTools VideoStudio 0.1.1

A CyUI desktop video studio for AI generation, ComfyUI workflows, motion design, RTX/DLSS upscaling, stereo 3D and Gaussian splats. Built on CyToolsCore with MCP, SDK and CLI access. FFmpeg, ComfyUI, PyTorch, NVIDIA components and model weights are optional installations.

[Project overview](../README.md) · [Français](../README.fr.md) · [Windows setup](../LISEZ-MOI.md) · [Validation evidence](Validation.md)

## Installation

Use Windows x64 and Python 3.11. In a packaged build, run `./app/install.ps1` once from the extracted root, then open `CyTools_VideoStudio.exe`. The package carries wheels for CyToolsCore and CyUI. For a source checkout, pass the platform-specific CyToolsCore 0.12.0 wheel with `-CoreWheel`. To use a local CyUI source checkout or wheel, pass `-CyUISource`.

To build the launcher, run `app/build_launcher.ps1` from an x64 Visual Studio Developer PowerShell. Current builds use that launcher to start the application's private Python. Application files and the version live under `app/`. Jobs, logs and preferences live under `data/`. Installed runtimes and downloaded packages live under `runtime/`.

For Linux testing, run `sh app/install_linux.sh /path/to/cytools_core-0.12.0-linux-wheel.whl` with the actual native wheel path, then run `sh CyTools_VideoStudio.sh`. This path targets portable FFmpeg and ComfyUI. Linux execution and macOS support remain to be validated.

## Operations and validation

| Area | Operations | Requirements and evidence |
| --- | --- | --- |
| Video | Probe, preview a frame, trim, resize, preserve stereo packing, swap eyes and encode H.264, HEVC or ProRes | FFmpeg and FFprobe. Windows probe, preview, packed stereo resize and encoding tested. |
| Motion | Animate a still image with pan, zoom and easing. Run animation and video API workflows | FFmpeg motion tested. Model-based motion uses a configured ComfyUI graph, nodes and weights. Create graphs in the ComfyUI editor. |
| Generation | Text, image/keyframe and reference conditioning, guides, validation, chained clips, Unreal scene packages and Storyboard films | MiniMax H3 bridge, its private runtime and licensed models. Frame extraction tested. Full generation is pending end-to-end validation. |
| Workflows | Run API-format graphs with typed value and local file bindings, inspect nodes and models, and collect artifacts. Import editor-format graphs and bundles through ComfyFlow | A private real ComfyUI workflow and an external checkpoint path were tested on Windows. Editor imports use a linked ComfyFlow installation. |
| Stereo 3D | Package eye pairs, estimate depth, analyse conversion, synthesize stereo, use Real-ESRGAN and register compatible custom checkpoints | Linked VideoToStereo3D. Packing and CUDA stereo conversion tested on Windows. |
| Gaussian | Video analysis, scene reconstruction, 4DAnyone synchronized views, SHARP sequences and C4G. Inspect point clouds in the 3D Viewer and open supported temporal scenes with Gaussian Player | Linked VideoToGaussian. Analysis and a synthetic point-cloud preview tested. Reconstruction and model inference await end-to-end validation in VideoStudio. |
| Enhance | Flat/VR packing, conventional resize, RTX Video Super Resolution, DLSS SR, DLSS 5 NR, ReShade and encoding | Linked CyVR Python source and optional GPU components. CPU resize, RTX Video and DLSS SR tested on Windows. DLSS 5 NR and ReShade await validation in VideoStudio. |

In **Components**, select the root directory of each existing application. Engines keep their own dependency environments, model compatibility rules and licenses. For CyVR, select the Python source distribution with its native libraries and optional effects installed.

MiniMax H3 scene-prompt editing and CyVR export resumption are available in their companion applications. VideoStudio covers the primary processing operations. Input-size and RAM/VRAM estimates guide the scheduler, and FFmpeg applies thread limits. Throughput and memory use on long or high-resolution footage still need benchmarks. See [validation evidence](Validation.md).

## Models and ComfyUI

Choose an installed local server, a network endpoint or a private local ComfyUI. **Install private ComfyUI** downloads an official tagged source release and creates a separate Python environment, with a CPU or selected PyTorch build. A managed job starts its own server with private input/output paths, applies the compute and low-memory settings and stops the server after work. Sharing a running external server keeps its model cache warm.

Use **Model locations** to reference an existing model file, a folder for a particular model category or the models library of an existing ComfyUI installation. A file reference makes its parent folder visible for that model category. The workflow selects the model by the filename shown by ComfyUI. Paths work across drives and no model data is moved. For external or network ComfyUI, configure the filesystem on the server that actually loads the weights.

**Download model** transfers an HTTPS file into a selected directory, can resume interrupted transfers, accepts an optional published SHA-256 and adds the completed file to the path registry. Accept the publisher's license before starting. Use the publisher's own installer for gated models and register the resulting local file. CUDA Toolkit registration remains in **Options**. Optional PyTorch build selection installs the corresponding wheels in ComfyUI's private environment. NVIDIA driver and Toolkit installation use the publisher's installer. [ComfyUI installation](https://docs.comfy.org/installation/manual_install) · [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads)

For NVIDIA effects, attach the CyVR source installation and use its **Components** screen to install its RTX packages. Available DLSS/RTX modes depend on that engine, GPU and driver. The CPU FFmpeg path is independent. Choose `stereo_model_library_register` for an existing compatible VideoToStereo3D checkpoint. Gaussian operations expose their compatible checkpoint paths where supported. Other Gaussian packs and H3 weights use their component installers.

For a graph exported using **Save (API Format)**, set `workflow` to the JSON path. Optionally set `bindings` to entries with `node`, `input` and either `value` or `file`. The runtime imports files into the task and uploads them to the selected server. Start with the official Wan and LTX workflows from Components. The selected server must already have the graph's nodes and weights. Custom nodes run with the privileges of the ComfyUI account. Remote servers retain their own input and output files under their own lifecycle settings. [ComfyUI server API](https://github.com/Comfy-Org/ComfyUI/blob/master/openapi.yaml)

## SDK, CLI and MCP

`CyTool.json` lists the operation and backend contracts. Standalone controls and AI clients submit the same job parameters. Configure runtime prerequisites with **Components**. A host can set a network ComfyUI bearer token via `CYVIDEOSTUDIO_COMFY_TOKEN`. Operation parameters never select the host address or carry this token.

```powershell
$python = '.\runtime\python\Scripts\python.exe'
& $python .\app\tool.py describe
& $python .\app\tool.py describe-operation transcode_video
& $python .\app\tool.py invoke transcode_video --params-file .\app\params.json --export-dir D:\Exports --timeout 1800
& $python .\app\tool.py --data-dir .\data\mcp mcp
```

MCP stdio clients pass an absolute Python path, absolute `app/tool.py` and `mcp`. From the desktop, open **Components → Local MCP and SDK** and start the local server to share its queue. Copy the displayed MCP address into the client or use `tool.py --connect http://127.0.0.1:8767/v1` with the desired CLI command. In headless use, run `serve` with the Core access profiles. `tool.py` uses the native CyMCPSDK transport shipped in the 0.12.0 wheel. Temporary AI tasks import local files and delete task-owned files at cleanup. Export wanted results before finishing the task. Standalone sessions keep their results in the job directory.

For a local Python SDK client, add this `app` directory to the interpreter's import path and import `create_runtime` from `tool`. Use the runtime's client, session, submit and wait APIs. External native clients use the same versioned schema through the Core's communication SDK.

## Updates

App updates accept a GitHub release ZIP and its published SHA-256. Staging checks the application manifest. Restart after the requested app switch, or use the previous-version action in **Components**. A switch preserves `data` and the private runtime. The checked release ZIP format contains `app/` at its root. A new base dependency or native launcher version requires running the installer from the full release ZIP.

## License

Copyright © 2026 Cyberalien. VideoStudio is licensed under [GNU Affero General Public License v3.0](../LICENSE) (`AGPL-3.0-only`). CyToolsCore, CyMCPSDK and CyUI are distributed under their respective MIT licenses in [LICENCES](../LICENCES).
