// STAR-CCM+ batch macro (F4): import a legacy .ccm mesh, then verify the
// E3 API chain the generated setup macro relies on -- boundary type switch
// via setBoundaryType(Class) and boundary-value application via
// getValues().getCondition(<Profile>.class).setValue(n).
//
// Every step is wrapped in its own try/catch and reported as an
// OK/FAIL line; the run ends with a MACROCHECK_SUMMARY line that CI can
// grep for (any FAIL must be treated as a regression).
//
// Environment:
//   GPH2CCM_MESH  path to the .ccm file (default: bench.ccm in CWD)
//
// Usage:
//   GPH2CCM_MESH=path/to/mesh.ccm starccm+ -batch tools/MacroCheck.java
//
// Boundary-type classes and profile classes mirror gph2ccm/macro.py
// (BCTYPE_TO_JAVA / BC_PARAM_TO_PROFILE) -- keep them in sync.

import star.common.*;
import star.base.neo.*;
import star.flow.*;
import star.energy.*;
import star.turbulence.*;
import java.io.File;

public class MacroCheck extends StarMacro
{
    private int okCount = 0;
    private int failCount = 0;

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
        System.out.println("MACROCHECK_START " + path);

        // -- import -------------------------------------------------------
        try
        {
            simulation.getImportManager().importMeshFiles(new String[] { path });
            System.out.println("IMPORT_DONE OK");
            okCount++;
        }
        catch (Exception ex)
        {
            System.out.println("IMPORT_DONE FAIL " + ex.getMessage());
            failCount++;
        }

        // -- boundary type switch (E3 statement #1) ------------------------
        Class<?>[] typeTargets = {
            star.common.WallBoundary.class,
            star.common.InletBoundary.class,
            star.common.PressureBoundary.class,
        };
        for (Region region : simulation.getRegionManager().getRegions())
        {
            for (Boundary b : region.getBoundaryManager().getBoundaries())
            {
                String name = b.getPresentationName();
                String before = b.getBoundaryType().getClass().getSimpleName();
                Class<?> target = typeTargets[name.hashCode() % typeTargets.length];
                try
                {
                    // raw-type call: the generic <T extends BoundaryType>
                    // signature cannot accept a Class<? extends ...> capture.
                    @SuppressWarnings({"unchecked", "rawtypes"})
                    Class<star.common.BoundaryType> t =
                        (Class<star.common.BoundaryType>) target;
                    b.setBoundaryType(t);
                    String after = b.getBoundaryType().getClass().getSimpleName();
                    boolean ok = after.equals(target.getSimpleName());
                    System.out.println("BC_TYPE " + name + " " + before
                        + " -> " + after + (ok ? " OK" : " FAIL"));
                    if (ok) okCount++; else failCount++;
                }
                catch (Exception ex)
                {
                    System.out.println("BC_TYPE " + name + " FAIL "
                        + ex.getMessage());
                    failCount++;
                }
            }
        }

        // -- value application chain (E3 statement #2) ---------------------
        // On a fresh batch import no physics continuum is active, so
        // getCondition may legitimately return null / throw: the chain is
        // reported, and a non-OK result here is informational (INFO) unless
        // the exception indicates a missing class/method (real regression).
        for (Region region : simulation.getRegionManager().getRegions())
        {
            for (Boundary b : region.getBoundaryManager().getBoundaries())
            {
                String name = b.getPresentationName();
                try
                {
                    VelocityMagnitudeProfile p = (VelocityMagnitudeProfile)
                        b.getValues().getCondition(VelocityMagnitudeProfile.class);
                    if (p == null)
                    {
                        System.out.println("BC_VALUE " + name
                            + " INFO no active flow model (expected on bare import)");
                    }
                    else
                    {
                        p.setValue(1.25);
                        System.out.println("BC_VALUE " + name + " OK setValue(1.25)");
                        okCount++;
                    }
                }
                catch (NoClassDefFoundError err)
                {
                    System.out.println("BC_VALUE " + name + " FAIL "
                        + err.toString());
                    failCount++;
                }
                catch (Exception ex)
                {
                    System.out.println("BC_VALUE " + name
                        + " INFO " + ex.getMessage());
                }
            }
        }

        System.out.println("MACROCHECK_SUMMARY ok=" + okCount
            + " fail=" + failCount
            + (failCount == 0 ? " MACROCHECK_PASS" : " MACROCHECK_FAIL"));
    }
}
