import { useEffect, useState } from "react";
import { Alert, Box, Button, Chip, MenuItem, Paper, Stack, TextField, Typography } from "@mui/material";
import { getDiagnostics, getRunAnalytics, type DiagnosticPage, type Issue, type Metric, type PlanMetrics, type RunAnalytics } from "./analytics-api";
import type { OptimizedRoute } from "./types";

export const labels: Record<string, string> = {coverage: "Cobertura", vehicles_used: "Vehículos", routed_orders: "Pedidos ruteados", unrouted_orders: "Pedidos no ruteados", distance_meters: "Distancia", driving_seconds: "Conducción", waiting_seconds: "Espera", service_seconds: "Servicio", total_duration_seconds: "Duración operativa", operating_cost: "Costo operativo estimado", allocated_orders: "Pedidos asignados a CD", valid_input_orders: "Pedidos de entrada válidos", window_compliance: "Cumplimiento de ventanas", window_compliant_orders: "Pedidos dentro de ventana", km_per_routed_order: "Distancia por pedido ruteado", operating_cost_per_routed_order: "Costo por pedido ruteado"};
export const metricText = (m: Metric) => m.value === null ? `No disponible · ${m.unavailable_reason ?? "sin dato"}` : `${m.value} ${m.unit}`;
Object.assign(labels, {maximum:"Utilización máxima",time_weighted_average:"Utilización promedio ponderada",initial_queue:"Espera inicial en cola",total_elapsed:"Tiempo total reconstruido (parcial)",durable_total_elapsed:"Tiempo hasta acuse durable",active_all_attempts:"Tiempo activo de todos los intentos"});
export function JsonDetails({title, value}: {title: string; value: unknown}) {
  return <details className="audit-details"><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
export function MetricGrid({metrics}: {metrics: Record<string, Metric>}) {
  return <Box className="count-grid">{Object.entries(metrics).map(([key, m]) => <Box className="count-cell" key={key}>
    <Typography variant="caption">{labels[key] ?? key}</Typography><Typography sx={{overflowWrap: "anywhere"}}>{metricText(m)}</Typography>
    <Typography variant="caption" sx={{display:"block"}} color="text.secondary">Denominador: {m.denominator ?? "no aplica / no disponible"}</Typography>
    {m.classification && <Typography variant="caption" sx={{display:"block"}}>Clasificación: {m.classification}</Typography>}
    <JsonDetails title="Fuente y versión" value={{provenance: m.provenance, version: m.calculation_version}} />
  </Box>)}</Box>;
}
export function IssueList({issues}: {issues: Issue[]}) {
  return <Stack spacing={1}>{issues.map((issue, i) => <Paper className="count-cell" key={`${issue.code}-${i}`}>
    <Typography>{issue.order_id ?? "Plan"} · {issue.code}</Typography><Typography variant="caption">{issue.stage} · {issue.certainty} · {issue.severity ?? issue.role ?? "Diagnóstico"}</Typography>
    <Typography>{issue.detail}</Typography><Typography variant="caption">Alcance: {issue.scope ?? String(issue.evidence?.scope ?? "ver evidencia")}</Typography>
    <JsonDetails title="Evidencia de la decisión" value={issue.evidence} />
  </Paper>)}</Stack>;
}
export function RouteFacts({routes, metrics, selected, onSelect}: {routes: OptimizedRoute[]; metrics: PlanMetrics; selected?: string | null; onSelect?: (id: string | null) => void}) {
  return <Stack spacing={2}>{routes.map(route => <Paper className="count-cell" key={route.source_vehicle_id}>
    <Button onClick={() => onSelect?.(selected === route.source_vehicle_id ? null : route.source_vehicle_id)}>{route.source_vehicle_id} · {route.distribution_center_id}</Button>
    <Typography>Salida: {route.departure_condition?.departure_at ?? route.steps[0]?.departure_at ?? "No disponible"}</Typography>
    <Typography variant="caption">Condición: {route.departure_condition?.policy ?? "LEGACY_NOT_RECORDED"} · {route.departure_condition?.scope ?? "metadato no registrado"}</Typography>
    <details><summary>Secuencia, horarios y cargas</summary><Box className="analytics-table"><table><thead><tr>{["Paso", "Pedido / CD", "Llegada", "Inicio servicio", "Salida", "Conducción s", "Espera s", "Servicio s", "Carga posterior"].map(s => <th key={s}>{s}</th>)}</tr></thead><tbody>{route.steps.map(step => <tr key={step.sequence}>
      <td>{step.sequence} · {step.kind}</td><td>{step.order_id ?? route.distribution_center_id}</td><td>{step.arrival_at}</td><td>{step.service_start_at ?? "No disponible"}</td><td>{step.departure_at ?? "No disponible"}</td><td>{step.travel_seconds_from_previous ?? "No disponible"}</td><td>{step.waiting_seconds ?? "No disponible"}</td><td>{step.service_seconds ?? "No disponible"}</td><td>{step.load_after ? JSON.stringify(step.load_after) : "No disponible"}</td>
    </tr>)}</tbody></table></Box></details>
    {metrics.routes.filter(m => m.source_vehicle_id === route.source_vehicle_id).map(m => <Box key={m.source_vehicle_id}>
      <details><summary>KPIs y utilización por dimensión</summary><MetricGrid metrics={m.metrics} />{Object.entries(m.utilization).map(([dimension, value]) => <Box key={dimension}><Typography>{dimension} · máximo y promedio ponderado por duración</Typography><MetricGrid metrics={value} /></Box>)}</details>
    </Box>)}
  </Paper>)}</Stack>;
}
export function ExportButtons({resource, id}: {resource: "runs" | "comparisons"; id: string}) {
  return <Box><Stack direction="row" spacing={1}><Button component="a" href={`/api/v1/${resource}/${id}/exports/csv`} download>Descargar CSV (ZIP)</Button><Button component="a" href={`/api/v1/${resource}/${id}/exports/xlsx`} download>Descargar XLSX</Button></Stack><Typography variant="caption">Archivos de consulta desde el resultado persistido; el navegador guarda la descarga. CSV contiene una tabla por archivo dentro del ZIP.</Typography></Box>;
}
export function AnalyticsPanel({runId, status, selectedVehicleId, onSelectRoute}: {runId: string; status: string; selectedVehicleId?: string | null; onSelectRoute?: (id: string | null) => void}) {
  const [data, setData] = useState<RunAnalytics | null>(null), [error, setError] = useState("");
  const [page, setPage] = useState<DiagnosticPage | null>(null), [offset, setOffset] = useState(0);
  const [stage, setStage] = useState(""), [certainty, setCertainty] = useState(""), [code, setCode] = useState("");
  useEffect(() => {let live = true; setData(null); setError(""); getRunAnalytics(runId).then(v => {if(live) setData(v);}).catch(e => {if(live) setError(String(e));}); return () => {live=false;};}, [runId, status]);
  useEffect(() => {setOffset(0);}, [runId, stage, certainty, code]);
  useEffect(() => {let live=true; getDiagnostics(runId, offset, stage, certainty, code).then(v => {if(live) setPage(v);}).catch(e => {if(live) setError(String(e));}); return () => {live=false;};}, [runId, offset, stage, certainty, code, status]);
  return <Paper className="import-card"><Typography variant="h5">Analítica del plan estimado</Typography>{error && <Alert severity="error">{error}</Alert>}
    <Typography>No acredita entregas ejecutadas ni ahorro realizado. Las métricas provienen del backend.</Typography>
    {status === "CANCELED" && <Alert severity="warning">Plan histórico cancelado. Sus rutas se conservan como estimación; comprueba las reservas liberadas abajo.</Alert>}
    {data && <><Chip label={data.metrics.current_status} /><ExportButtons resource="runs" id={runId} />
      {data.metrics.plan ? <><MetricGrid metrics={data.metrics.plan.metrics} /><JsonDetails title="Objetivo VROOM (distinto del costo operativo completo)" value={data.metrics.plan.solver_objective} /><RouteFacts routes={data.run.result?.routes ?? []} metrics={data.metrics.plan} selected={selectedVehicleId} onSelect={onSelectRoute} /></> : <Alert severity="info">{data.metrics.unavailable_reason ?? "Plan no disponible"}</Alert>}
      <Typography variant="h6">Reservas actuales</Typography>{data.metrics.current_reservations.length ? data.metrics.current_reservations.map(r => <Chip key={r.order_id} label={`${r.order_id} · ${r.status}`} />) : <Typography>No existen reservas operacionales registradas para esta corrida.</Typography>}
      <Typography variant="h6">Tiempos de procesamiento (sin espera del usuario)</Typography><MetricGrid metrics={Object.fromEntries(Object.entries(data.metrics.processing).filter(([key])=>["initial_queue","total_elapsed","durable_total_elapsed","active_all_attempts"].includes(key))) as Record<string,Metric>} />
      <JsonDetails title="Tiempos de procesamiento, intentos y clasificación" value={data.metrics.processing} />
      <Typography variant="caption">El total reconstruido finaliza antes del COMMIT; el acuse durable es desconocido. No incluye la espera del usuario para aceptar o cancelar.</Typography>
      <JsonDetails title="Procedencia y versiones del cálculo" value={data.metrics.plan?.operating_cost ?? data.run.input} />
    </>}
    <Typography variant="h6" sx={{mt: 2}}>Diagnósticos paginados</Typography><Stack direction={{xs: "column", sm: "row"}} spacing={1}>
      <TextField select label="Etapa" sx={{minWidth:160}} value={stage} onChange={e => setStage(e.target.value)}>{["", "ALLOCATION", "OPTIMIZATION", "OPERATIONAL"].map(s => <MenuItem key={s} value={s}>{s || "Todas"}</MenuItem>)}</TextField>
      <TextField select label="Certeza" sx={{minWidth:150}} value={certainty} onChange={e => setCertainty(e.target.value)}>{["", "PROVEN", "INFERRED"].map(s => <MenuItem key={s} value={s}>{s || "Todas"}</MenuItem>)}</TextField><TextField label="Código exacto" value={code} onChange={e => setCode(e.target.value)} />
    </Stack>{page && <><Typography>{page.total} diagnósticos · desde {offset + 1}</Typography><IssueList issues={page.items} />{page.unavailable_reason && <Alert severity="info">{page.unavailable_reason}</Alert>}<Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset-5))}>Diagnósticos anteriores</Button><Button disabled={page.next_offset === null} onClick={() => setOffset(page.next_offset!)}>Siguientes diagnósticos</Button><JsonDetails title="Procedencia del diagnóstico" value={page.provenance} /></>}
  </Paper>;
}
