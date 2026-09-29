import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  CircularProgress,
  CssBaseline,
  Divider,
  Paper,
  Stack,
  ThemeProvider,
  Typography,
  createTheme,
} from "@mui/material";

import { createDemoRun, getLatestRun } from "./api";
import { formatEstimatedCost } from "./formatters";
import { routeColorForVehicle } from "./map-data";
import { RouteMap } from "./RouteMap";
import { RouteSequence } from "./RouteSequence";
import type { PlanningRun } from "./types";
import "./styles.css";

const theme = createTheme({
  palette: {
    mode: "dark",
    background: { default: "#08111f", paper: "#101d2d" },
    primary: { main: "#33d6a6" },
    warning: { main: "#ffb454" },
    text: { primary: "#eef6ff", secondary: "#91a4ba" },
  },
  typography: {
    fontFamily: 'Inter, "Segoe UI", system-ui, sans-serif',
    h4: { fontWeight: 720, letterSpacing: "-0.035em" },
    button: { textTransform: "none", fontWeight: 700 },
  },
  shape: { borderRadius: 14 },
  components: {
    MuiPaper: {
      styleOverrides: {
        root: { backgroundImage: "none", border: "1px solid rgba(148, 169, 194, 0.12)" },
      },
    },
  },
});

function formatDuration(hours: number): string {
  const totalMinutes = Math.round(hours * 60);
  return `${Math.floor(totalMinutes / 60)}h ${totalMinutes % 60}m`;
}

function KpiCard({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <Paper className="kpi-card" elevation={0}>
      <Typography variant="overline" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="h5" sx={{ fontWeight: 750 }}>
        {value}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {note}
      </Typography>
    </Paper>
  );
}

export default function App() {
  const [run, setRun] = useState<PlanningRun | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);

  useEffect(() => {
    getLatestRun()
      .then(setRun)
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setBusy(false));
  }, []);

  const execute = async () => {
    setBusy(true);
    setError(null);
    setSelectedVehicleId(null);
    try {
      setRun(await createDemoRun());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Optimization failed");
    } finally {
      setBusy(false);
    }
  };

  const completed = useMemo(
    () =>
      run?.completed_at
        ? new Intl.DateTimeFormat("en", {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(run.completed_at))
        : "No completed run",
    [run],
  );
  const kpis = run?.kpis;
  const routes = run?.result?.routes ?? [];
  const unassigned = run?.result?.unassigned ?? [];

  useEffect(() => {
    if (
      selectedVehicleId &&
      !routes.some((route) => route.source_vehicle_id === selectedVehicleId)
    ) {
      setSelectedVehicleId(null);
    }
  }, [routes, selectedVehicleId]);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box className="shell">
        <Box component="header" className="topbar">
          <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
            <Box className="brand-mark">R</Box>
            <Box>
              <Typography sx={{ fontWeight: 800, lineHeight: 1.1 }}>
                RouteOps
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Network Control Center
              </Typography>
            </Box>
          </Stack>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <Chip size="small" label="SYNTHETIC DATA" color="primary" variant="outlined" />
            <Chip size="small" label="SANTIAGO · CL" variant="outlined" />
          </Stack>
        </Box>

        <Box component="main" className="content">
          <Box className="headline">
            <Box>
              <Typography variant="overline" color="primary.main">
                Daily planning workspace
              </Typography>
              <Typography variant="h4">Last-mile route plan</Typography>
              <Typography color="text.secondary" sx={{ mt: 0.6 }}>
                Stock-aware allocation and closed-route optimization from two demo centers.
              </Typography>
            </Box>
            <Stack spacing={1} sx={{ alignItems: "flex-end" }}>
              <Button
                variant="contained"
                color="primary"
                size="large"
                onClick={execute}
                disabled={busy}
                startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
              >
                {busy ? "Planning routes…" : "Run optimization"}
              </Button>
              <Typography variant="caption" color="text.secondary">
                {completed}
              </Typography>
            </Stack>
          </Box>

          {error && (
            <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box className="kpi-grid">
            <KpiCard
              label="Vehicles used"
              value={String(kpis?.vehicles_used ?? "—")}
              note={`${kpis?.routes ?? 0} closed routes`}
            />
            <KpiCard
              label="Assigned"
              value={String(kpis?.assigned_orders ?? "—")}
              note={`${kpis?.unassigned_orders ?? 0} need review`}
            />
            <KpiCard
              label="Network distance"
              value={kpis ? `${kpis.distance_km.toFixed(1)} km` : "—"}
              note="OSRM road distance"
            />
            <KpiCard
              label="Plan duration"
              value={kpis ? formatDuration(kpis.total_hours) : "—"}
              note="Drive + wait + service"
            />
            <KpiCard
              label="Service time"
              value={kpis ? formatDuration(kpis.service_hours) : "—"}
              note="At delivery stops"
            />
            <KpiCard
              label="Waiting time"
              value={kpis ? formatDuration(kpis.waiting_hours) : "—"}
              note="Before time windows"
            />
            <KpiCard
              label="Solver time"
              value={kpis ? `${kpis.solver_time_ms} ms` : "—"}
              note="VROOM solve duration"
            />
            <KpiCard
              label="Estimated cost"
              value={
                kpis
                  ? formatEstimatedCost(kpis.estimated_cost, kpis.currency)
                  : "—"
              }
              note="Vehicle cost model"
            />
          </Box>

          <Box className="workspace-grid">
            <Paper className="map-panel" elevation={0}>
              <Box className="panel-heading">
                <Box>
                  <Typography sx={{ fontWeight: 750 }}>Route network</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Optimized geometry from the pinned OSRM dataset
                  </Typography>
                </Box>
                {run && <Chip label={run.status} size="small" color="primary" />}
              </Box>
              <RouteMap run={run} selectedVehicleId={selectedVehicleId} />
            </Paper>

            <Stack spacing={2} sx={{ minWidth: 0 }}>
              <Paper className="side-panel" elevation={0}>
                <Box className="panel-heading">
                  <Typography sx={{ fontWeight: 750 }}>Routes</Typography>
                  <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                    <Typography variant="caption" color="text.secondary">
                      {routes.length} active
                    </Typography>
                    <Button
                      size="small"
                      variant="text"
                      disabled={selectedVehicleId === null}
                      onClick={() => setSelectedVehicleId(null)}
                    >
                      Mostrar todas
                    </Button>
                  </Stack>
                </Box>
                <Divider />
                <Box className="scroll-list">
                  {routes.length === 0 && (
                    <Typography className="empty-state" color="text.secondary">
                      Run the optimizer to generate routes.
                    </Typography>
                  )}
                  {routes.map((route) => (
                    <ButtonBase
                      className={`route-row route-row-button${
                        selectedVehicleId === route.source_vehicle_id
                          ? " route-row-selected"
                          : ""
                      }`}
                      key={route.vehicle_id}
                      aria-pressed={selectedVehicleId === route.source_vehicle_id}
                      onClick={() =>
                        setSelectedVehicleId((current) =>
                          current === route.source_vehicle_id
                            ? null
                            : route.source_vehicle_id,
                        )
                      }
                    >
                      <Box
                        className="route-dot"
                        sx={{ backgroundColor: routeColorForVehicle(route.source_vehicle_id) }}
                      />
                      <Box sx={{ minWidth: 0, flex: 1 }}>
                        <Typography noWrap sx={{ fontWeight: 700 }}>
                          {route.source_vehicle_id}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {route.distribution_center_id} · {route.steps.filter((step) => step.kind === "DELIVERY").length} stops
                        </Typography>
                      </Box>
                      <Typography variant="body2" sx={{ fontWeight: 650 }}>
                        {(route.totals.distance_meters / 1000).toFixed(1)} km
                      </Typography>
                    </ButtonBase>
                  ))}
                </Box>
              </Paper>

              <Paper className="side-panel" elevation={0}>
                <Box className="panel-heading">
                  <Typography sx={{ fontWeight: 750 }}>Stop sequence</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Persisted route steps
                  </Typography>
                </Box>
                <Divider />
                <RouteSequence routes={routes} />
              </Paper>

              <Paper className="side-panel" elevation={0}>
                <Box className="panel-heading">
                  <Typography sx={{ fontWeight: 750 }}>Exceptions</Typography>
                  <Chip
                    size="small"
                    label={unassigned.length}
                    color={unassigned.length > 0 ? "warning" : "default"}
                  />
                </Box>
                <Divider />
                <Box className="scroll-list compact">
                  {unassigned.length === 0 && (
                    <Typography className="empty-state" color="text.secondary">
                      No unassigned orders.
                    </Typography>
                  )}
                  {unassigned.map((item) => (
                    <Box className="exception-row" key={item.order_id}>
                      <Stack
                        direction="row"
                        sx={{ justifyContent: "space-between", gap: 1 }}
                      >
                        <Typography sx={{ fontWeight: 700 }}>{item.order_id}</Typography>
                        <Chip size="small" label={item.reasons[0]?.certainty ?? item.stage} />
                      </Stack>
                      <Typography variant="caption" color="warning.main">
                        {item.reasons[0]?.code}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                        {item.reasons[0]?.detail}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              </Paper>
            </Stack>
          </Box>

          <Box component="footer" className="footer">
            <Typography variant="caption" color="text.secondary">
              {run?.result
                ? `VROOM ${run.result.solver.engine_version} · OSRM ${run.result.solver.routing_engine_version} · ${run.result.solver.solve_duration_ms} ms`
                : "VROOM + OSRM solver stack"}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Map © OpenStreetMap contributors · All business data is synthetic
            </Typography>
          </Box>
        </Box>
      </Box>
    </ThemeProvider>
  );
}
