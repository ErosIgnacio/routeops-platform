import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import type { Revision, Scenario } from "./import-api";
import {
  PlanningApiError,
  acceptRun,
  cancelRun,
  createRun,
  getRun,
  listDemos,
  listRevisions,
  listRuns,
  listScenarios,
  lookupRun,
  prepareDemo,
  type DemoOption,
  type RevisionRun,
} from "./planning-api";
import { RouteMap } from "./RouteMap";
import { RouteSequence } from "./RouteSequence";

const STORAGE_KEY = "routeops.planning.v1";
type Draft = { scenarioId: string; revisionNo: number; key: string; runId: string };

function savedDraft(): Draft {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as Partial<Draft> | null;
    return {
      scenarioId: raw?.scenarioId ?? "",
      revisionNo: raw?.revisionNo ?? 0,
      key: raw?.key ?? "",
      runId: raw?.runId ?? "",
    };
  } catch {
    return { scenarioId: "", revisionNo: 0, key: "", runId: "" };
  }
}

const problemNames: Record<string, string> = {
  STOCK_NO_FULL_COVERAGE: "Ningún CD cubre todas las líneas con su stock disponible.",
  NO_COMPATIBLE_VEHICLE: "No hay vehículo compatible en un CD con stock suficiente.",
  SOLVER_NO_FEASIBLE_ROUTE: "VROOM no incluyó el pedido en una ruta factible.",
  ROUTING_DEPENDENCY_FAILED: "OSRM no pudo completar la evaluación.",
  SOLVER_DEPENDENCY_FAILED: "VROOM no pudo completar la optimización.",
  SOLVER_RESPONSE_INVALID: "VROOM devolvió una respuesta incompatible con la corrida.",
  SOLVER_INPUT_INVALID: "No se pudo preparar el problema para VROOM.",
  PLANNING_INFRASTRUCTURE_FAILED: "Falló la infraestructura de planificación.",
  SOLVER_WORKLOAD_LIMIT: "La revisión excede el límite medido para una corrida.",
  REVISION_NOT_CURRENT: "Solo la última revisión publicada admite nuevas corridas.",
  SCENARIO_NOT_EXECUTABLE: "La revisión necesita pedidos, centros y vehículos para ejecutarse.",
  RUN_IDEMPOTENCY_CONFLICT: "Esta clave ya corresponde a una solicitud diferente.",
  RUN_TRANSITION_CONFLICT: "La corrida ya pasó a un estado incompatible con esa acción.",
};

function describeError(reason: unknown): string {
  if (reason instanceof PlanningApiError) return problemNames[reason.code] ?? reason.code;
  return "No se pudo completar la solicitud. Puedes recuperar el resultado con la misma clave.";
}

export function PlanningWorkspace() {
  const [draft, setDraft] = useState<Draft>(savedDraft);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [history, setHistory] = useState<RevisionRun[]>([]);
  const [demos, setDemos] = useState<DemoOption[]>([]);
  const [demo, setDemo] = useState("");
  const [quality, setQuality] = useState("BALANCED");
  const [policy, setPolicy] = useState("alternatives-v2");
  const [run, setRun] = useState<RevisionRun | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => localStorage.setItem(STORAGE_KEY, JSON.stringify(draft)), [draft]);
  useEffect(() => {
    listScenarios().then((page) => setScenarios(page.items)).catch((reason) => setError(describeError(reason)));
    listDemos().then(setDemos).catch((reason) => setError(describeError(reason)));
  }, []);
  useEffect(() => {
    if (!draft.scenarioId) return;
    listRevisions(draft.scenarioId)
      .then((page) => setRevisions(page.items))
      .catch((reason) => setError(describeError(reason)));
    listRuns(draft.scenarioId)
      .then((page) => setHistory(page.items))
      .catch((reason) => setError(describeError(reason)));
  }, [draft.scenarioId]);

  const refresh = useCallback(async (runId: string) => {
    const current = await getRun(runId);
    setRun(current);
    setHistory((previous) => previous.map((item) => item.run_id === runId ? current : item));
    setDraft((previous) => ({ ...previous, runId: current.run_id }));
    return current;
  }, []);

  useEffect(() => {
    if (draft.runId) {
      refresh(draft.runId).catch((reason) => setError(describeError(reason)));
    } else if (draft.key && draft.scenarioId && draft.revisionNo) {
      lookupRun(draft.scenarioId, draft.revisionNo, draft.key)
        .then((existing) => {
          setRun(existing);
          setDraft((previous) => ({ ...previous, runId: existing.run_id }));
        })
        .catch((reason) => {
          if (!(reason instanceof PlanningApiError && reason.status === 404)) {
            setError(describeError(reason));
          }
        });
    }
  }, []); // Recover the saved operation once when the page opens.

  useEffect(() => {
    if (!run || !["QUEUED", "RUNNING"].includes(run.status)) return;
    const timer = window.setInterval(() => {
      refresh(run.run_id).catch((reason) => setError(describeError(reason)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [run?.run_id, run?.status, refresh]);

  async function start() {
    if (!draft.scenarioId || !draft.revisionNo || busy) return;
    setBusy("start");
    setError("");
    const key = draft.key || crypto.randomUUID();
    setDraft((previous) => ({ ...previous, key }));
    try {
      const created = await createRun(draft.scenarioId, draft.revisionNo, key, quality, policy);
      setRun(created);
      setDraft((previous) => ({ ...previous, key, runId: created.run_id }));
      setHistory((previous) => [created, ...previous.filter((item) => item.run_id !== created.run_id)]);
    } catch (reason) {
      setError(describeError(reason));
    } finally {
      setBusy("");
    }
  }

  async function recover() {
    if (!draft.scenarioId || !draft.revisionNo || !draft.key) return;
    setBusy("recover");
    try {
      const existing = await lookupRun(draft.scenarioId, draft.revisionNo, draft.key);
      setRun(existing);
      setDraft((previous) => ({ ...previous, runId: existing.run_id }));
      setError("");
    } catch (reason) {
      setError(describeError(reason));
    } finally {
      setBusy("");
    }
  }

  async function transition(action: "accept" | "cancel") {
    if (!run || busy) return;
    setBusy(action);
    try {
      const changed = action === "accept" ? await acceptRun(run.run_id) : await cancelRun(run.run_id);
      setRun(changed);
      setHistory((previous) => previous.map((item) => item.run_id === changed.run_id ? changed : item));
      setError("");
    } catch (reason) {
      setError(describeError(reason));
      await refresh(run.run_id).catch(() => undefined);
    } finally {
      setBusy("");
    }
  }

  async function prepare() {
    if (!demo || busy) return;
    if (demo === "original") { window.location.assign("/"); return; }
    setBusy("demo");
    setError("");
    try {
      const prepared = await prepareDemo(demo);
      const next = { scenarioId: prepared.scenario_id, revisionNo: prepared.revision.revision_no, key: "", runId: "" };
      setDraft(next);
      setRun(null);
      setSelectedVehicleId(null);
      const page = await listScenarios();
      setScenarios(page.items);
      setRevisions([prepared.revision]);
      setHistory([]);
    } catch (reason) {
      setError(describeError(reason));
    } finally {
      setBusy("");
    }
  }

  const routes = run?.result?.routes ?? [];
  const exceptions = run?.result?.unassigned ?? [];

  return <Box className="shell">
    <Box component="header" className="topbar">
      <Typography sx={{ fontWeight: 800 }}>RouteOps · Planificación</Typography>
      <Stack direction="row" spacing={1}>
        <Button href="/">Tablero original</Button>
        <Button href="/imports">Importaciones</Button>
      </Stack>
    </Box>
    <Box component="main" className="content">
      <Typography variant="h4" sx={{ mb: 1 }}>Corridas de revisiones publicadas</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        La asignación registra el CD y reserva stock; VROOM prepara rutas antes de la aceptación.
      </Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
      <Stack spacing={2}>
        <Paper className="side-panel">
          <Typography variant="h6" sx={{ mb: 1 }}>Preparar una demo aislada</Typography>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
            <TextField select label="Demo" value={demo} onChange={(event) => setDemo(event.target.value)} sx={{ minWidth: 290 }}>
              {demos.map((item) => <MenuItem key={item.id} value={item.id}>{item.title}</MenuItem>)}
            </TextField>
            <Button variant="outlined" disabled={!demo || !!busy} onClick={prepare}>
              {busy === "demo" ? "Preparando…" : demo === "original" ? "Abrir demo original" : "Crear escenario demo"}
            </Button>
          </Stack>
          <Typography variant="caption" color="text.secondary">Cada preparación crea un escenario nuevo; no altera ni libera reservas previas.</Typography>
        </Paper>
        <Paper className="side-panel">
          <Typography variant="h6" sx={{ mb: 1 }}>Nueva corrida</Typography>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ flexWrap: "wrap" }}>
            <TextField select label="Escenario" value={scenarios.some((item) => item.id === draft.scenarioId) ? draft.scenarioId : ""} onChange={(event) => {
              setDraft({ scenarioId: event.target.value, revisionNo: 0, key: "", runId: "" }); setRun(null);
            }} sx={{ minWidth: 230 }}>
              {scenarios.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
            </TextField>
            <TextField select label="Revisión" value={revisions.some((item) => item.revision_no === draft.revisionNo) ? draft.revisionNo : ""} onChange={(event) => {
              setDraft((previous) => ({ ...previous, revisionNo: Number(event.target.value), key: "", runId: "" })); setRun(null);
            }} sx={{ minWidth: 150 }}>
              {revisions.map((item) => <MenuItem key={item.id} value={item.revision_no}>Revisión {item.revision_no}</MenuItem>)}
            </TextField>
            <TextField select label="Calidad" value={quality} onChange={(event) => setQuality(event.target.value)} sx={{ minWidth: 140 }}>
              {["FAST", "BALANCED", "THOROUGH"].map((item) => <MenuItem key={item} value={item}>{item}</MenuItem>)}
            </TextField>
            <TextField select label="Política" value={policy} onChange={(event) => setPolicy(event.target.value)} sx={{ minWidth: 175 }}>
              <MenuItem value="alternatives-v2">Alternativas v2</MenuItem>
              <MenuItem value="greedy-v1">Greedy v1</MenuItem>
            </TextField>
          </Stack>
          <Stack direction="row" spacing={1} sx={{ mt: 2, alignItems: "center" }}>
            <Button variant="contained" disabled={!draft.scenarioId || !draft.revisionNo || !!busy || !!run} onClick={start}>Solicitar corrida</Button>
            <Button disabled={!draft.key || !!busy} onClick={recover}>Recuperar solicitud</Button>
            <Button disabled={!!busy} onClick={() => { setDraft((previous) => ({ ...previous, key: "", runId: "" })); setRun(null); }}>Nueva clave</Button>
            {busy && <CircularProgress size={18} />}
          </Stack>
          {draft.key && <Typography variant="caption" color="text.secondary">Clave de reintento guardada: {draft.key}</Typography>}
        </Paper>

        {run && <Paper className="side-panel">
          <Stack direction="row" spacing={1} sx={{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
            <Box><Typography variant="h6">Corrida {run.run_id.slice(0, 8)}</Typography><Typography variant="body2" color="text.secondary">Revisión {revisions.find((item) => item.id === run.scenario_revision_id)?.revision_no ?? "histórica"} · intento {run.attempts}/{run.max_attempts}</Typography></Box>
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <Chip color={run.status === "FAILED" ? "error" : run.status === "READY" ? "warning" : "primary"} label={run.status} />
              {run.status === "READY" && <Button variant="contained" disabled={!!busy} onClick={() => transition("accept")}>Aceptar</Button>}
              {["QUEUED", "RUNNING", "READY"].includes(run.status) && <Button color="warning" disabled={!!busy} onClick={() => transition("cancel")}>Cancelar</Button>}
              <Button onClick={() => refresh(run.run_id).catch((reason) => setError(describeError(reason)))}>Actualizar</Button>
            </Stack>
          </Stack>
          {run.status === "READY" && <Alert severity="info" sx={{ mt: 1 }}>Las rutas están listas para revisión. Sus reservas ruteadas siguen HELD y no caducan automáticamente.</Alert>}
          {run.error && <Alert severity="error" sx={{ mt: 1 }}>{problemNames[run.error] ?? run.error}</Alert>}
          <Typography variant="body2" sx={{ mt: 1 }}>Snapshot {run.inventory_snapshot_id.slice(0, 8)} · Política {run.input.allocation_policy as string}</Typography>
        </Paper>}

        <Box className="workspace-grid">
          <Paper className="map-panel"><Box className="panel-heading"><Typography sx={{ fontWeight: 750 }}>Rutas de la revisión</Typography><Typography variant="caption">{routes.length} rutas</Typography></Box><RouteMap run={run} selectedVehicleId={selectedVehicleId} /></Paper>
          <Stack spacing={2}>
            <Paper className="side-panel"><Typography variant="h6">Rutas</Typography>
              {routes.map((route) => <Button key={route.vehicle_id} onClick={() => setSelectedVehicleId(route.source_vehicle_id === selectedVehicleId ? null : route.source_vehicle_id)}>{route.source_vehicle_id} · {route.distribution_center_id} · {route.steps.filter((step) => step.kind === "DELIVERY").length} entregas</Button>)}
              <RouteSequence routes={routes} />
            </Paper>
            <Paper className="side-panel"><Typography variant="h6">Asignación y reservas</Typography>
              {run?.decisions.map((decision) => <Box key={decision.order_id} className="exception-row">
                <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}><Typography sx={{ fontWeight: 700 }}>{decision.order_id} → {decision.center_id ?? "Sin CD"}</Typography><Chip size="small" label={decision.reservation_status ?? "Sin reserva"} /></Stack>
                {decision.reason_code && <Typography color="warning.main" variant="body2">{decision.reason_code}: {problemNames[decision.reason_code]}</Typography>}
                <Typography variant="caption" color="text.secondary">{decision.evidence.policy_version} · {decision.evidence.candidates.map((item) => `${item.center_id}: ${item.discard_reason ?? "elegido"}${item.duration_seconds === null ? "" : `, ${item.duration_seconds}s`}`).join(" · ")}</Typography>
              </Box>)}
            </Paper>
            <Paper className="side-panel"><Typography variant="h6">Excepciones</Typography>
              {exceptions.map((item) => <Box key={item.order_id} className="exception-row"><Typography sx={{ fontWeight: 700 }}>{item.order_id} · {item.stage}</Typography><Typography variant="body2">{problemNames[item.reasons[0]?.code ?? ""] ?? item.reasons[0]?.code}</Typography><Chip size="small" label={item.reasons[0]?.certainty === "PROVEN" ? "Comprobado" : "Inferido"} /></Box>)}
            </Paper>
          </Stack>
        </Box>
        <Paper className="side-panel"><Typography variant="h6">Historial del escenario</Typography>
          {history.map((item) => <Button key={item.run_id} onClick={() => { setRun(item); setDraft((previous) => ({ ...previous, runId: item.run_id, key: "" })); }}>{item.run_id.slice(0, 8)} · {item.status} · {new Date(item.started_at).toLocaleString()}</Button>)}
          {history.length === 0 && <Typography color="text.secondary">Aún no hay corridas.</Typography>}
        </Paper>
      </Stack>
    </Box>
  </Box>;
}
