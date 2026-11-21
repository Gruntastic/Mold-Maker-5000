# Casting Mold Maker

Turn a watertight STL, OBJ, or 3MF solid into two matching mold halves. The app saves two binary STL files in a new subfolder, ready to import into a slicer. Both files contain only the mold geometry. Their mating faces share the same plane and coordinates.

## Run on Windows

Double-click **Casting Mold Maker 7.exe** in the `dist` folder or run **Open Mold Maker.cmd**. The bundled app runs on 64-bit Windows and includes Python and its dependencies. It draws at the Windows system DPI. Moving the window to a monitor with a different scaling factor can cause Windows to stretch it.

## Make a mold

1. Open **Direct Cast** or **Silicone Brace** and choose a source model. The app checks the mesh, then builds the selected tab’s preview automatically. Switching tabs or changing settings regenerates the preview when needed. STL and OBJ coordinates are read as millimeters. 3MF dimensions are converted from the file’s declared units.
2. Set the wall thickness. The default is 5 mm. The exterior follows the model and can be thicker around corners and recesses.
3. Set the split axis and position. The percentage is measured from the model’s minimum to its maximum along that axis. A 50% X split produces left and right halves in the source orientation. Mold coordinates are centered around the original model’s bounding box center.
4. Set the pour-hole diameter. The app looks for a horizontal section that can fit the opening and cuts the channel upward along +Z. Use a smaller diameter if the opening does not fit, or enter 0 for a mold without a pour channel.
5. Open **Ports…** to enter a pour center as `X, Y` or add vents as `X, Y, diameter`, one vent per line. X and Y are measured in millimeters from the model’s bounding-box center. The channels run along +Z from the cavity to the exterior. Leave the pour center blank for automatic placement, or set the pour diameter to 0 to omit the inlet while keeping any vents.
6. Enable **Add alignment keys** to put two pins on the positive half and matching sockets on the negative half. **Key clearance (mm)** applies radially and at the socket floor. The default is 0.25 mm. If the app cannot fit two keys, try a thicker wall, move the split, or reduce the clearance. Dry-fit the printed halves before casting.
7. Select the assembled mold, the exploded mold, or either half from the **View** dropdown. Drag to rotate, right-drag to pan, and scroll to zoom. The OpenGL preview keeps the original triangles for meshes up to one million faces. The app may simplify larger previews. In software mode, dense meshes display as sampled points. STL export uses the generated mold geometry.
8. Click **Save mold STLs…** and select a folder in the save prompt. The app saves both halves in a new subfolder and shows its location.

The app accepts a single closed, connected solid. A rigid mold can trap castings with undercuts. Choose a parting plane that lets both halves pull away, or use a flexible mold for shapes that lock into a rigid one. Use clamps to hold the halves closed during casting. Place vents at high points where air could collect.

For dense models, the app may simplify the exterior within its offset tolerance or build the detailed outer contour in sections. Direct-cast mode can use a convex outer envelope when the contour exceeds the section budget. The status message identifies when this happens. The casting cavity is cut from the original mesh.

## Check and repair a source mesh

Open **Mesh Check** after loading a file. The report lists open edges, non-manifold edges, duplicate and degenerate faces, winding issues, and the number of closed shells when available. **Repair mesh** welds tiny gaps, removes duplicate or degenerate faces, corrects winding, and fills small holes and suitable convex planar openings. Review the updated report and the source preview after repair.

A successful repair is used by all mold tabs. **Save repaired STL…** writes a separate copy. Disconnected solids, difficult intersections, and complex holes may need manual editing. The checker tests mesh topology and mold compatibility. It does not certify that the mesh is free of self-intersections.

## Cast a silicone liner in a printed brace

1. Open **Silicone Brace** and load the model you will use as the positive master. Set **Silicone liner (mm)** for the gap around the master and **Wall thickness (mm)** for the rigid printed shell. The liner can be thicker around corners and where narrow recesses close up.
2. Choose a split that lets the printed brace open around the liner. The silicone fill hole starts in the space above the master and runs toward +Z. Use a smaller diameter if the hole does not fit at the chosen thickness.
3. Open **Silicone liner cutaway** in the preview to check the gap. Export and print the two `brace_` STLs.
4. Dry-fit the master and hold it centered in the jacket with a removable fixture. Make sure the master clears the jacket on every side. Clamp and seal the jacket seam. Leave an air path around the fill tube and vent any isolated high points.
5. Pour silicone into the gap. Once it cures, open the jacket and remove the master through an opening or a controlled slit in the rubber. Prepare the casting inlet, then return the liner to the printed jacket for support while casting.

For highly detailed meshes, silicone mode asks for a simplified source if it cannot keep a thin gap around the contours.

## Make a jacket for brush-on silicone

1. Open **Brush-On** and load the positive master with the intended opening facing down along −Z. **Open base height (%)** sets the bottom edge of the jacket and coat preview. At 0% it sits at the lowest point on the master. Raising it leaves the lower portion outside the jacket, so use that setting when that portion can be left uncovered or supported separately.
2. Set **Silicone coat (mm)** to the planned cured thickness and **Jacket clearance (mm)** to the extra space for fit. The preview uses a rounded offset, and the status reports the estimated volume of the coat. The quantity you mix also needs to account for application losses and variations in coat thickness.
3. Set the jacket wall thickness, choose an X or Y split, and set **Clamp flange (mm)** for a lip along the seam. Enter 0 to omit the flange. Optional alignment keys register the two printed halves. If the app cannot build two connected halves, try a different split or reduce the flange width.
4. Check the **Silicone coat cutaway**, then select **Save jacket STLs…** to export the two `jacket_` files. The removal check samples short, straight pulls of the jacket halves. Check deep undercuts and curved or rounded bases by dry-fitting before casting.
5. Brush the silicone onto the original master following the silicone supplier’s instructions. After curing, dry-fit the printed halves around the coat and check that they close without squeezing it or leaving large unsupported gaps. Open the jacket to remove the master. Prepare an opening in the silicone for casting, then reassemble and clamp the supported mold.

## Build from source and verify the export

From the project folder on 64-bit Windows with Python 3.14 installed, run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller==6.22.3
.\.venv\Scripts\python.exe test_mold.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name "Casting Mold Maker 7" --manifest (Resolve-Path windows.manifest).Path --specpath build --collect-data trimesh mold_maker.py
```
