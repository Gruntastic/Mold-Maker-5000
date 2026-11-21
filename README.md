# Casting Mold Maker

Turn a watertight STL, OBJ, or 3MF solid into two matching mold halves or a single open-face mold. The app saves the mold as binary STL files in a new subfolder, ready to import into a slicer. When you export two halves, their mating faces share the same plane and coordinates.

## Run on Windows

Double-click **Casting Mold Maker 10.exe** in the `dist` folder or run **Open Mold Maker.cmd**. The bundled app runs on 64-bit Windows and includes Python and its dependencies. It draws at the Windows system DPI. Moving the window to a monitor with a different scaling factor can cause Windows to stretch it.

## Make a mold

1. Open **Direct Cast** or **Silicone Brace** and choose a source model. The app checks the mesh, then builds the selected tab’s preview automatically. Switching tabs or changing settings regenerates the preview when needed. STL and OBJ coordinates are read as millimeters. 3MF dimensions are converted from the file’s declared units.
2. Set the wall thickness. The default is 5 mm. The exterior follows the model and can be thicker around corners and recesses.
3. Set the split axis and position. The percentage is measured from the model’s minimum to its maximum along that axis. A 50% X split produces left and right halves in the source orientation. Mold coordinates are centered around the original model’s bounding box center.
4. Set the pour-hole diameter. The app looks for a horizontal section that can fit the opening and cuts the channel upward along +Z. Use a smaller diameter if the opening does not fit, or enter 0 for a mold without a pour channel.
5. Open **Ports…** to enter a pour center as `X, Y` or add vents as `X, Y, diameter`, one vent per line. X and Y are measured in millimeters from the model’s bounding-box center. The channels run along +Z from the cavity to the exterior. Leave the pour center blank for automatic placement, or set the pour diameter to 0 to omit the inlet while keeping any vents.
6. Enable **Add alignment keys** to put two pins on the positive half and matching sockets on the negative half. **Key clearance (mm)** applies radially and at the socket floor. The default is 0.25 mm. If the app cannot fit two keys, try a thicker wall, move the split, or reduce the clearance. Dry-fit the printed halves before casting.
7. Select **Exploded mold** to see the source part between the two halves. **Positive half** and **Negative half** start with the mating face toward you. Drag to rotate, right-drag to pan, and scroll to zoom. The OpenGL preview keeps the original triangles for meshes up to one million faces. The app may simplify larger previews. In software mode, dense meshes display as sampled points. STL export uses the generated mold geometry.
8. Click **Save mold STLs…** and select a folder in the save prompt. The app saves both halves in a new subfolder and shows its location.

The app accepts a single closed, connected solid. A rigid mold can trap castings with undercuts. Choose a parting plane that lets both halves pull away, or use a flexible mold for shapes that lock into a rigid one. Use clamps to hold the halves closed during casting. Place vents at high points where air could collect.

For dense models, the app may simplify the exterior within its offset tolerance or build the detailed outer contour in sections. Direct-cast mode can use a convex outer envelope when the contour exceeds the section budget. The status message identifies when this happens. The casting cavity is cut from the original mesh.

## Check and repair a source mesh

Open **Mesh Check** after loading a file. The report lists open edges, non-manifold edges, duplicate and degenerate faces, winding issues, and the number of closed shells when available. **Repair mesh** welds tiny gaps, removes duplicate or degenerate faces, corrects winding, and fills small holes and suitable convex planar openings. Review the updated report and the source preview after repair.

A successful repair is used by all mold tabs. **Save repaired STL…** writes a separate copy. Disconnected solids, difficult intersections, and complex holes may need manual editing. The checker tests mesh topology and mold compatibility. It does not certify that the mesh is free of self-intersections.

## Cast a silicone liner in a printed brace

1. Open **Silicone Brace** and load the model you will use as the positive master. Set **Silicone liner (mm)** for the gap around the master and **Wall thickness (mm)** for the rigid printed shell. The liner can be thicker around corners and where narrow recesses close up.
2. Choose a split that lets the brace and liner halves open around the master. Choose **Exploded silicone liner** to see the two liner halves around the master. **Save mold STLs…** exports the rigid braces. **Save liner STLs…** exports the two flexible liner shapes separately for inspection or fabrication.
3. To pour two separate liner halves, set the pour-hole diameter to 0 and clear any vents before exporting the braces. Place one brace half with its mating face up. Dry-fit the master with a removable fixture that holds it at the correct depth and centered in the brace. Fill the exposed gap to the mating face and let it cure. Repeat with the other brace half.
4. Open the brace to remove the master. Prepare the casting inlet through the liner, then seat both silicone halves in their matching brace halves and clamp the assembly.

For highly detailed meshes, silicone mode asks for a simplified source if it cannot keep a thin gap around the contours.

## Brush-cast small parts in an open mold

1. Open **Open-Face** and choose a model. Set the **Opening direction** to the side you want exposed for brushing. For a relief with a flat back, point the opening toward the back.
2. Set **Opening plane (%)** to choose where the cavity ends. The percentage runs from the deepest point toward the opening. Moving the plane deeper can expose a wider opening and leaves the model above that plane out of the casting. At 100% a flat-backed piece can retain its full depth at its original scale.
3. Set **Shell thickness (mm)** and inspect the **Open mold** preview. It opens at a raised angle so you can see the casting surface. If the preview shows an overhang warning, lower the opening plane or change direction for easier brush access and release.
4. Click **Save open mold STL…** to export a single contoured shell, with its lowest point at Z = 0 and its casting surface facing up. Brush resin or silicone onto that surface to make an open-backed piece. The coats determine the thickness of the casting.

## Build from source and verify the export

From the project folder on 64-bit Windows with Python 3.14 installed, run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller==6.22.3
.\.venv\Scripts\python.exe test_mold.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name "Casting Mold Maker 10" --manifest (Resolve-Path windows.manifest).Path --specpath build --collect-data trimesh mold_maker.py
```
