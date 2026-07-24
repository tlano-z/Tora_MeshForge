# Tora_MeshForge

[日本語](README.md) | [English](README.en.md) | [GitHub](https://github.com/tlano-z/Tora_MeshForge) | [Report an issue](https://github.com/tlano-z/Tora_MeshForge/issues)

Tora_MeshForge is a Windows tool that converts dense or AI-generated static 3D models into FBX files with practical triangle counts and editable UVs. It can compare several candidates or build one model at a selected triangle target.

## What it does

- Tries multiple triangle counts and recommends Fidelity, Balanced, and Lightweight candidates
- Builds one FBX at a selected triangle count
- Rebuilds fragmented UVs into more coherent islands
- Reconstructs Base Color and creates a Normal map from lost source-shape detail
- Creates Geometry / Mesh / Texture / UV previews for the source and results
- Checks geometry, UVs, textures, materials, and independent FBX reload
- Shows estimated time, progress, logs, cancellation, and result links in the GUI

## Supported models

- Input formats: FBX, GLB/glTF, and OBJ
- Intended for static models
- Rig, animation, and shape keys are detected but are not transferred to generated models.
- Reconstructed materials include Base Color and a newly generated Shape Normal. Source Roughness, Metallic, Emission, Alpha, and existing Normal maps are not transferred or combined.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer
- Blender 4.2 LTS or newer
- Internet access during the first installation

Blender is available from the [official website](https://www.blender.org/download/). Common installation locations are detected automatically.

## Install and launch

1. Extract the GitHub ZIP into a folder that will remain in place.
2. Double-click `Install-Tora_MeshForge.bat`.
3. Confirm that the final status is `READY`.
4. Double-click `Tora_MeshForge.bat`.

See the [installation guide](docs/installation.md) if installation does not complete.

## Basic use

### 1. Choose the input and output

- `Input model`: Source model
- `Texture override (optional)`: Use when the model has one source texture that cannot be resolved automatically
- `Output FBX`: Destination for a single build, or the name used for the Quality Sweep output directory

These are normally the only required fields. Open `Show advanced paths` only when changing Blender or work-directory paths. Open `Show inspection findings` when detailed source-model metrics are needed.

### 2. Run a workflow

| Goal | Setting | Run button |
|---|---|---|
| The appropriate triangle count is unknown, or several results should be compared | Candidate list in `Quality Sweep` | `Run Quality Sweep` |
| The required triangle count is already known | Preset and target in `Single Target Build` | `Run Single Target Build` |

Quality Sweep is recommended for a model that has not been processed before. Its standard candidates are 50,000 / 25,000 / 10,000 / 5,000 triangles.

Shape Normal, texture resolution, and UV margin in `Shared output settings` apply to both workflows. Quality Sweep ignores the Single Target value, and Single Target Build ignores the Sweep candidate list.

### 3. Wait for completion

`Workflow monitor` shows the current stage, progress, estimated total time, elapsed time, and approximate remaining time. UV search can take several minutes or longer depending on the model. Use `Cancel` when the operation must be stopped.

When processing finishes, `Results` shows the result HTML, model name, and output folder. Click the HTML or folder link to open the result.

## Review the result

For Quality Sweep, open `final-evaluation.html` and compare SOURCE with:

- `Fidelity`: Candidate closest to the source model
- `Balanced`: Candidate balancing quality and triangle count
- `Lightweight`: Lightest candidate that completed successfully

For Single Target Build, open the `*.evaluation.html` written beside the output FBX and compare SOURCE with the generated result:

- Geometry / Mesh / Texture previews
- UV-only and UV-over-Base-Color layouts
- Shape Normal and invalid-projection masks
- Automatic check results

An automatic PASS does not replace visual review of silhouette, thin parts, contacting surfaces, textures, and practical UV editability.

## Optional individual operations

Open `Show manual operations` only when inspecting an individual stage or artifact. Normal conversion does not require these controls.

| Button | Purpose |
|---|---|
| Inspect | Review model structure and triangle counts without changing the model |
| Static FBX Round Trip | Check FBX export and reload without changing geometry |
| Fast Optimize | Apply simple reduction while retaining existing UVs and materials |
| Runtime Rebuild | Rebuild UV and Base Color with limited movement from the source surface |
| Surface Retopology | Build one model at a selected triangle count |
| Triangle Sweep | Build and compare several triangle counts |

## Main output files

A single build writes the output FBX together with Base Color, Shape Normal, UV images, SOURCE/output previews in three directions and four display modes, a processing report, and `*.evaluation.html`.

Quality Sweep writes candidate FBX files and images, SOURCE previews, SOURCE UV-over-Base-Color, comparison results, and `final-evaluation.html`.

## CLI

Build one model:

```powershell
.\.venv\Scripts\tora-meshforge.exe process `
  --mode surface-retopology `
  --input "C:\models\source.fbx" `
  --texture "C:\models\atlas.jpg" `
  --output "C:\models\source.10k.fbx" `
  --target-triangles 10000 `
  --texture-resolution 2048
```

Compare multiple candidates:

```powershell
.\.venv\Scripts\tora-meshforge.exe sweep `
  --input "C:\models\source.fbx" `
  --texture "C:\models\atlas.jpg" `
  --output-directory "C:\models\source-sweep" `
  --triangle-targets 50000 25000 10000 5000 `
  --texture-resolution 2048
```

## License

Tora_MeshForge is available under the [MIT License](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for included dependencies.
