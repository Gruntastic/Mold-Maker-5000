# Casting Mold Maker

Turn a watertight STL, OBJ, or 3MF model into a negative mold. **Direct Cast** creates two rectangular box halves that separate at a vertical seam. **Open-Face** creates an exposed impression for brush-on work. You can also save the positive model as a separate STL.

## Run on Windows

Double-click **Casting Mold Maker 16.exe** in the `dist` folder or run **Open Mold Maker.cmd**. The bundled app runs on 64-bit Windows and includes Python and its dependencies. It draws at the Windows system DPI. Moving the window to a monitor with a different scaling factor can cause Windows to stretch it.

## Make a mold

1. Open **Direct Cast** to make a two-piece mold for casting resin or silicone, and choose a source model. The app checks the mesh and builds a preview automatically. STL and OBJ coordinates are read as millimeters. 3MF dimensions are converted from the file’s declared units.
2. Set the wall thickness. The default is 5 mm. Contoured molds add width around the cavity for a flat mating flange.
3. Choose **Front direction** along X or Y. Both halves extend through the box's full height. **Follow model contour** is on by default. It traces a mating surface through the part so curved shells have a raised core on one half and a matching recess on the other. The transition from those features is smoothed into a flat perimeter. **Split position (%)** sets the height of that flat seam. Turn contour following off to use a single flat vertical split.
4. Leave the pour hole on **Auto** to size a top opening that reaches the cavity. Enter a diameter in millimeters to set the size yourself, or enter 0 to omit the inlet.
5. Open **Ports…** to enter a pour center as `X, Y` or add vents as `X, Y, diameter`, one vent per line. X and Y are measured in millimeters from the model’s bounding-box center. The channels run along +Z from the cavity to the exterior. Leave the pour center blank for automatic placement, or set the pour diameter to 0 to omit the inlet while keeping any vents.
6. Enable **Add alignment keys** for two pins and sockets in the flat flange. The default **Key fit** is −0.10 mm radially, with tapered, slotted pins designed to flex and grip their sockets. A value closer to zero loosens the hold. Positive values give a slip fit. The sockets leave extra depth for the mating faces to close flush. Dry-fit the printed halves and adjust the fit for your printer and material.
7. **Exploded mold** shows the two negative halves pulled apart with the positive model in the center. **Front and back** lays both cavity faces up with the same end at the top. **Fit in back half** shows the positive in its exact seated position. **Assembled mold** shows the closed box. Drag to rotate, right-drag to pan, and scroll to zoom.
8. Click **Save mold STLs…** to export two separate files, `mold_front.stl` and `mold_back.stl`.

The app accepts a single closed, connected solid. The contoured seam is sampled from the model. The cavity itself is cut from the original mesh. Overlapping features along the pull direction may still trap a rigid casting. Use the warning and a physical dry fit to check release, or use a flexible mold for shapes that lock in. Use clamps to hold the halves closed and place vents at high points where air could collect.

Use **Positive model** to view the part by itself and **Save positive STL…** to save a separate printable copy from any tab. It retains the source scale and orientation and is centered to match the Direct Cast assembly. The source file stays intact.

## Check and repair a source mesh

Open **Mesh Check** after loading a file. The report lists open edges, non-manifold edges, duplicate and degenerate faces, winding issues, and the number of closed shells when available. **Repair mesh** welds tiny gaps, removes duplicate or degenerate faces, corrects winding, and fills small holes and suitable convex planar openings. Review the updated report and the source preview after repair.

A successful repair is used by all mold tabs. **Save repaired STL…** writes a separate copy. Disconnected solids, difficult intersections, and complex holes may need manual editing. The checker tests mesh topology and mold compatibility. It does not certify that the mesh is free of self-intersections.

## Cast silicone around a preprinted master

1. In **Direct Cast**, load the model you already printed and select **Cast silicone around a printed master**. Set **Silicone gap (mm)** to leave room for the silicone around it. The gap can be thicker around corners and where narrow recesses close up.
2. Add a pour hole and vents. The inlet leads into the gap beside the master. **Save mold STLs…** exports the two vertically split box pieces as `brace_front.stl` and `brace_back.stl`.
3. Dry-fit the master and hold it in the center of the gap with a removable fixture. Clamp and seal the brace halves, then pour silicone into the gap. Once it cures, separate the brace halves and open or slit the flexible liner as needed to release the master.
4. If the rigid braces would catch on the master, use **Save liner STLs…** to get two negative inserts for separate fabrication in a flexible material. They seat inside the matching braces. Plan the casting inlet through the inserts before casting.

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
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name "Casting Mold Maker 16" --manifest (Resolve-Path windows.manifest).Path --specpath build --collect-data trimesh mold_maker.py
```
