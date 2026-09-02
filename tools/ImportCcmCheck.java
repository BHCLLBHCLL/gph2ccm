// STAR-CCM+ batch macro: import a legacy .ccm mesh and print mesh statistics.
//
// The mesh path is taken from the GPH2CCM_MESH environment variable, falling
// back to "bench.ccm" in the working directory (so it works unchanged inside
// the self-hosted CI, which generates bench.ccm first).
//
// Usage:
//   GPH2CCM_MESH=path/to/mesh.ccm starccm+ -batch tools/ImportCcmCheck.java

import star.common.*;
import java.io.File;

public class ImportCcmCheck extends StarMacro
{
    @Override
    public void execute()
    {
        String path = System.getenv("GPH2CCM_MESH");
        if (path == null || path.trim().isEmpty())
        {
            path = "bench.ccm";
        }
        Simulation simulation = null;
        try
        {
            simulation = new Simulation((String) null);
        }
        catch (Exception ex)
        {
            simulation = new Simulation("");
        }
        System.out.println("IMPORT_START " + path);
        simulation.getImportManager().importMeshFiles(new String[] { path });
        System.out.println("IMPORT_DONE");

        for (Object iface : simulation.getInterfaceManager().getObjects())
        {
            System.out.println(
                "INTERFACE " + iface.getClass().getSimpleName()
                + " " + iface.toString()
            );
        }

        FvRepresentation fvRepresentation = (FvRepresentation)
            simulation.getRepresentationManager().getDefaultFvRepresentation();
        FvRegionManager fvRegionManager = fvRepresentation.getFvRegionManager();

        for (Region region : simulation.getRegionManager().getRegions())
        {
            System.out.println("REGION " + region.getPresentationName());
            FvRegion fvRegion = fvRegionManager.getFvRegion(region);
            System.out.println("CELLS " + fvRegion.getCellCount());
            System.out.println("VERTICES " + fvRegion.getVertexCount());
            System.out.println("INTERIOR_FACES " + fvRegion.getInteriorFaceCount());
            for (Boundary boundary : region.getBoundaryManager().getBoundaries())
            {
                FvBoundary fvBoundary =
                    fvRegion.getFvBoundaries().getFvBoundary(boundary);
                System.out.println(
                    "BOUNDARY " + boundary.getPresentationName()
                    + " FACES " + fvBoundary.getFaceCount()
                );
            }
        }
    }
}
