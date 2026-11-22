# Casting Mold Maker

Turn a watertight STL, OBJ, or 3MF solid into a matching front-and-back mold pair or an open impression for brush-on work. The app saves binary STL files in a new subfolder. The two halves of a mold pair share their mating plane and assembly coordinates.

## Run on Windows

Double-click **Casting Mold Maker 12.exe** in the `dist` folder or run **Open Mold Maker.cmd**. The bundled app runs on 64-bit Windows and includes Python and its dependencies. It draws at the Windows system DPI. Moving the window to a monitor with a different scaling factor can cause Windows to stretch it.

## Make a mold

1. Open **Direct Cast** to make a two-piece mold for casting resin or silicone, and choose a source model. The app checks the mesh and builds a preview automatically. STL and OBJ coordinates are read as millimeters. 3MF dimensions are converted from the file’s declared units.
2. Set the wall thickness. The default is 5 mm. The exterior follows the model and can be thicker around corners and recesses.
3. Set **Front direction** to the direction the front of your model faces. Set **Split position (%)** to move the mating plane from the back toward the front. At 50% the split is centered. Exported mold coordinates are centered around the model’s original bounding box center.
4. Add a pour hole if needed. The default diameter is 0 mm. With a positive diameter, the app looks for a horizontal section that can fit the opening and cuts the channel upward along +Z. Use a smaller diameter if the opening does not fit.
5. Open **Ports…** to enter a pour center as `X, Y` or add vents as `X, Y, diameter`, one vent per line. X and Y are measured in millimeters from the model’s bounding-box center. The channels run along +Z from the cavity to the exterior. Leave the pour center blank for automatic placement, or set the pour diameter to 0 to omit the inlet while keeping any vents.
6. Enable **Add alignment keys** for two pins and matching sockets. **Key clearance (mm)** applies radially and at the socket floor. The default is 0.25 mm. If the app cannot fit two keys, try a thicker wall, move the split, or reduce the clearance. Dry-fit the printed halves before casting.
7. **Exploded mold** shows both mold halves pulled apart along the split direction with the part in the center. **Front and back** lays both cavity faces up for inspection. **Assembled mold** uses their actual assembly orientation. Drag to rotate, right-drag to pan, and scroll to zoom. The app may simplify previews with more than one million faces. STL export uses the generated mold geometry.
8. Click **Save mold STLs…** to export two separate files, `mold_front.stl` and `mold_back.stl`.

The app accepts a single closed, connected solid. A rigid mold can trap castings with undercuts. Choose a parting plane that lets both halves pull away, or use a flexible mold for shapes that lock into a rigid one. Use clamps to hold the halves closed during casting. Place vents at high points where air could collect.

For dense models, the app may simplify the exterior within its offset tolerance or build the detailed outer contour in sections. Direct-cast mode can use a convex outer envelope when the contour exceeds the section budget. The status message identifies when this happens. The casting cavity is cut from the original mesh.

## Check and repair a source mesh

Open **Mesh Check** after loading a file. The report lists open edges, non-manifold edges, duplicate and degenerate faces, winding issues, and the number of closed shells when available. **Repair mesh** welds tiny gaps, removes duplicate or degenerate faces, corrects winding, and fills small holes and suitable convex planar openings. Review the updated report and the source preview after repair.

A successful repair is used by all mold tabs. **Save repaired STL…** writes a separate copy. Disconnected solids, difficult intersections, and complex holes may need manual editing. The checker tests mesh topology and mold compatibility. It does not certify that the mesh is free of self-intersections.

## Cast silicone around a preprinted master

1. In **Direct Cast**, load the model you already printed and select **Cast silicone around a printed master**. Set **Silicone gap (mm)** to leave room for the silicone around it. The gap can be thicker around corners and where narrow recesses close up.
2. Add a pour hole and vents. The inlet leads into the gap beside the master. **Save mold STLs…** exports `brace_front.stl` and `brace_back.stl`.
3. Dry-fit the master and hold it in the center of the gap with a removable fixture. Clamp and seal the brace halves, then pour silicone into the gap. Once it cures, separate the brace halves and open or slit the flexible liner as needed to release the master.

## Brush material into an open impression

1. Open **Open-Face** and set **Surface direction** to the side of the source model you want to reproduce. The app makes its negative impression facing up.
2. Set **Impression depth (%)** to choose how much of the selected side to capture. The default is 25%. A lower value keeps the rim shallower for brush access. **Mold thickness (mm)** sets the backing around the impression. If the preview warns about overhangs, reduce the depth or choose a different direction for easier access and release.
3. Click **Save brush-on mold STL…** to export the exposed impression. Brush resin or silicone into it. The impression preserves the original-scale detail of the selected surface.

## Build from source and verify the export

From the project folder on 64-bit Windows with Python 3.14 installed, run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller==6.22.3
.\.venv\Scripts\python.exe test_mold.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name "Casting Mold Maker 12" --manifest (Resolve-Path windows.manifest).Path --specpath build --collect-data trimesh mold_maker.py
```
