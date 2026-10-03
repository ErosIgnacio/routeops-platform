import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, Chip, MenuItem, Paper, Stack, TextField, Typography } from "@mui/material";
import { allScenarios, comparisonHistory, comparisonInput, createComparison, getComparison, type Comparison, type InputOptions, type Issue, type ManualRoute } from "./analytics-api";
import { ExportButtons, IssueList, JsonDetails, MetricGrid, RouteFacts, labels } from "./AnalyticsPanel";
import type { Revision, Scenario } from "./import-api";
import { listRevisions } from "./planning-api";
import { RouteMap } from "./RouteMap";
import type { PlanningRun } from "./types";
import { WorkspaceNav } from "./WorkspaceNav";

const STORE = "routeops.comparison.v1";
type Draft = {scenario: string; revision: number; routes: ManualRoute[]; key: string; id: string; quality: string};
const empty: Draft = {scenario: "", revision: 0, routes: [], key: "", id: "", quality: "BALANCED"};
function restore(): Draft {try {return {...empty, ...JSON.parse(localStorage.getItem(STORE) ?? "{}")} as Draft;} catch {return empty;}}
export function comparisonMap(value: Comparison, planName: string): PlanningRun {
  return {run_id: `${value.comparison_id}:${planName}`, scenario_name: `Comparación · ${planName}`, status: "READY", started_at: "", completed_at: null, input: {solution_quality: value.manual_input.solution_quality}, kpis: null, error: null,
    result: {routes: value.result!.alternatives[planName]!.routes, unassigned: [], solver: {engine: "VROOM", engine_version: "", routing_engine: "OSRM", routing_engine_version: "", solve_duration_ms: 0}}};
}
export function ComparisonWorkspace() {
  const [draft, setDraft] = useState<Draft>(restore), [scenarios, setScenarios] = useState<Scenario[]>([]), [revisions, setRevisions] = useState<Revision[]>([]);
  const [options, setOptions] = useState<InputOptions | null>(null), [comparison, setComparison] = useState<Comparison | null>(null);
  const [history, setHistory] = useState<Awaited<ReturnType<typeof comparisonHistory>> | null>(null), [offset, setOffset] = useState(0);
  const [planName, setPlanName] = useState("manual"), [vehicle, setVehicle] = useState<string | null>(null), [issueOffset, setIssueOffset] = useState(0);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  useEffect(() => {try {localStorage.setItem(STORE, JSON.stringify(draft));} catch {setError("No se pudo guardar la referencia local; conserva el ID antes de recargar.");}}, [draft]);
  useEffect(() => {allScenarios().then(setScenarios).catch(e => setError(String(e)));}, []);
  useEffect(() => {if(!draft.scenario) return; let live = true; listRevisions(draft.scenario).then(p => {if(live) setRevisions(p.items);}).catch(e => setError(String(e))); return () => {live=false;};}, [draft.scenario]);
  useEffect(() => {if(!draft.scenario || !draft.revision) {setOptions(null); return;} let live = true; comparisonInput(draft.scenario, draft.revision).then(v => {if(live) setOptions(v);}).catch(e => setError(String(e))); return () => {live=false;};}, [draft.scenario, draft.revision]);
  useEffect(() => {if(!draft.scenario) return; let live=true; comparisonHistory(draft.scenario, offset).then(v => {if(live) setHistory(v);}).catch(e => setError(String(e))); return () => {live=false;};}, [draft.scenario, offset, comparison?.status, comparison?.comparison_id]);
  useEffect(() => {
    if(!draft.id) return; let live = true; let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {try {const v = await getComparison(draft.id); if(!live) return; setComparison(v); setDraft(d=>d.key ? d : {...d,routes:v.manual_input.manual_routes,quality:v.manual_input.solution_quality,revision:v.manual_input.revision_no}); if(["QUEUED", "RUNNING"].includes(v.status)) timer = setTimeout(refresh, 1000);} catch(e) {if(live) setError(String(e));}};
    void refresh(); return () => {live=false; if(timer) clearTimeout(timer);};
  }, [draft.id]);
  useEffect(() => {setVehicle(null); setIssueOffset(0);}, [planName, comparison?.comparison_id]);
  const selected = comparison?.result?.alternatives[planName];
  const mapRun = useMemo(() => comparison?.result && selected ? comparisonMap(comparison, planName) : null, [comparison, selected, planName]);
  const used = draft.routes.flatMap(r => r.order_ids), pending = options?.orders.filter(id => !used.includes(id)) ?? [];
  const duplicates = [...new Set(used.filter((id, i) => used.indexOf(id) !== i))];
  const unknown = used.filter(id => !options?.orders.includes(id));
  const locked = busy || !!draft.key || !!draft.id;
  const editRoute = (index: number, route: ManualRoute) => setDraft(d => ({...d, routes: d.routes.map((r, i) => i === index ? route : r)}));
  const submit = async () => {
    setBusy(true); setError("");
    const current = {...draft, key: draft.key || crypto.randomUUID()};
    try {
      // Persist the exact request/key before issuing HTTP, including a lost response.
      localStorage.setItem(STORE, JSON.stringify(current)); setDraft(current);
      const value = await createComparison(current.scenario, current.revision, current.key, {manual_routes: current.routes, solution_quality: current.quality});
      setComparison(value); setDraft(d => ({...d, id: value.comparison_id})); setOffset(0);
    } catch(e) {setError(e instanceof Error ? e.message : "Solicitud interrumpida; recupera con la misma clave.");} finally {setBusy(false);}
  };
  const selectHistory = (id: string) => {setComparison(null); setDraft(d => ({...d, id, key: ""}));};
  const allIssues: Issue[] = selected ? [...(selected.incidences ?? []), ...(selected.result?.unassigned ?? []).flatMap(item => item.reasons.map(reason => ({...reason, order_id: item.order_id, stage: item.stage})))] : [];
  return <Box className="shell"><WorkspaceNav title="Comparaciones analíticas" /><Box component="main" className="content import-content">
    <Typography variant="h4">Manual y políticas de asignación</Typography><Alert severity="info">Simulación: comparar no reserva ni consume inventario operacional. Todas las alternativas usan el mismo contexto congelado. No se declara un óptimo global.</Alert>
    {error && <Alert severity="error" onClose={() => setError("")}>{error}</Alert>}
    <Paper className="import-card"><Typography variant="h6">Nueva comparación desde revisión publicada</Typography>
      <Stack direction={{xs:"column", sm:"row"}} spacing={2}><TextField select label="Escenario" value={scenarios.some(s=>s.id===draft.scenario) ? draft.scenario : ""} disabled={locked} onChange={e => {setDraft({...empty, scenario:e.target.value});setComparison(null);setOffset(0);}} sx={{minWidth:230}}>{scenarios.map(s => <MenuItem value={s.id} key={s.id}>{s.name} · {s.id.slice(0,8)}</MenuItem>)}</TextField>
        <TextField select label="Revisión" disabled={locked} value={revisions.some(r=>r.revision_no===draft.revision) ? draft.revision : ""} onChange={e => setDraft(d => ({...d, revision:Number(e.target.value), routes:[]}))} sx={{minWidth:130}}>{revisions.map(r => <MenuItem value={r.revision_no} key={r.id}>Revisión {r.revision_no}</MenuItem>)}</TextField>
        <TextField select label="Calidad solver" disabled={locked} value={draft.quality} onChange={e => setDraft(d => ({...d, quality:e.target.value}))}>{["FAST","BALANCED","THOROUGH"].map(v => <MenuItem key={v} value={v}>{v}</MenuItem>)}</TextField>
      </Stack>
      <Typography sx={{mt:2}}>Secuencia manual exacta: un identificador por línea, en orden de entrega. Cambia el orden editando las líneas. No se ajusta automáticamente. Salida al inicio del turno efectivo; VROOM puede escoger otra hora.</Typography>
      {options && <><Typography>Pendientes: {pending.join(", ") || "ninguno"} · zona {options.timezone}</Typography>{duplicates.length > 0 && <Alert severity="warning">Duplicados: {duplicates.join(", ")}</Alert>}{unknown.length > 0 && <Alert severity="warning">Desconocidos: {unknown.join(", ")}</Alert>}
        {draft.routes.map((route, i) => <Paper className="count-cell" key={i}><Stack spacing={1} direction={{xs:"column", sm:"row"}}>
          <TextField label={`Vehículo ruta ${i+1}`} select value={route.vehicle_id} disabled={locked} onChange={e => {const v=options.vehicles.find(v => v.vehicle_id===e.target.value)!;editRoute(i,{...route, vehicle_id:v.vehicle_id,center_id:v.center_id});}}>{options.vehicles.map(v => <MenuItem key={v.vehicle_id} value={v.vehicle_id}>{v.vehicle_id}</MenuItem>)}</TextField>
          <TextField label={`CD ruta ${i+1}`} select value={route.center_id} disabled={locked} onChange={e => editRoute(i,{...route,center_id:e.target.value})}>{options.centers.map(c => <MenuItem key={c} value={c}>{c}</MenuItem>)}</TextField>
          <TextField label={`Secuencia ruta ${i+1}`} multiline minRows={2} value={route.order_ids.join("\n")} disabled={locked} onChange={e => editRoute(i,{...route,order_ids:e.target.value.split("\n").filter(id=>id!=="")})} sx={{flex:1}} />
          <Button disabled={locked} onClick={() => setDraft(d=>({...d,routes:d.routes.filter((_,j)=>i!==j)}))}>Quitar ruta {i+1}</Button>
        </Stack><Typography variant="caption">Inicio efectivo del vehículo: {options.vehicles.find(v=>v.vehicle_id===route.vehicle_id)?.shift_start ?? "desconocido"}</Typography></Paper>)}
        <Button disabled={locked || !options.vehicles.length || draft.routes.length>=6} onClick={() => {const v=options.vehicles[0]!;setDraft(d=>({...d,routes:[...d.routes,{vehicle_id:v.vehicle_id,center_id:v.center_id,order_ids:[]}]}));}}>Añadir ruta manual</Button>
      </>}
      <Stack direction={{xs:"column", sm:"row"}} spacing={1}><Button variant="contained" disabled={locked || !options} onClick={submit}>Crear comparación</Button><Button disabled={busy || !draft.key} onClick={submit}>Recuperar misma solicitud</Button><Button disabled={busy} onClick={()=>{setDraft(d=>({...d,key:"",id:""}));setComparison(null);}}>Nueva identidad de comparación</Button></Stack>
      {draft.key && <Typography variant="caption">Clave de recuperación: {draft.key}. La entrada queda fija hasta crear una nueva identidad.</Typography>}
    </Paper>
    {comparison && <Paper className="import-card"><Typography variant="h6">Comparación {comparison.comparison_id}</Typography><Chip label={comparison.status} /><Typography>Intentos: {comparison.attempts}. Sin porcentaje de progreso estimado.</Typography>
      <JsonDetails title="Historial de recuperación" value={comparison.history} />
      {comparison.result && <><ExportButtons resource="comparisons" id={comparison.comparison_id} /><TextField select label="Plan mostrado" value={planName} onChange={e=>setPlanName(e.target.value)} sx={{my:2,minWidth:240}}>{Object.keys(comparison.result.alternatives).map(n=><MenuItem key={n} value={n}>{n}</MenuItem>)}</TextField>
        <Alert severity={selected!.feasible ? "success":"warning"}>{planName} · {selected!.feasible ? "Plan viable (consultar cobertura)":"Plan inviable: métricas solo informativas"}. Cobertura: {selected!.metrics.metrics.coverage?.value ?? "No disponible"}. Un menor costo con menos pedidos no indica un ganador.</Alert>
        <Typography>Salida: {selected!.departure_policy ?? "Metadato no registrado en comparación anterior"}. Diferencias de salida y espera también cambian la duración; no atribuir todo a la secuencia.</Typography>
        <MetricGrid metrics={selected!.metrics.metrics} /><JsonDetails title="Objetivo VROOM separado del costo operativo" value={selected!.solver_objective} />
        <Paper className="map-panel"><Box className="panel-heading">Mapa del plan {planName} · {selected!.routes.length} rutas</Box><RouteMap run={mapRun} selectedVehicleId={vehicle} onShowAll={()=>setVehicle(null)} /></Paper>
        <RouteFacts routes={selected!.routes} metrics={selected!.metrics} selected={vehicle} onSelect={setVehicle} />
        <Typography variant="h6">Infracciones y pedidos omitidos · {allIssues.length}</Typography><IssueList issues={allIssues.slice(issueOffset,issueOffset+5)} /><Button disabled={issueOffset===0} onClick={()=>setIssueOffset(Math.max(0,issueOffset-5))}>Incidencias anteriores</Button><Button disabled={issueOffset+5>=allIssues.length} onClick={()=>setIssueOffset(issueOffset+5)}>Siguientes incidencias</Button>
        <JsonDetails title="Entrada evaluada y condiciones de salida" value={selected!.evaluated_input ?? comparison.manual_input} /><JsonDetails title="Decisiones de CD y candidatos" value={selected!.decisions} />
        <Typography variant="h6">Diferencias · candidato menos baseline</Typography>{Object.entries(comparison.result.comparisons).map(([pair,d])=><Box className="count-cell" key={pair}><Typography>{pair} · {d.comparability} · {d.scope}</Typography><Typography>{d.savings_claim_allowed ? "Ambos planes viables con cobertura completa; diferencias descriptivas, sin ganador automático." : "No corresponde afirmar ahorro comparable."}</Typography><Box className="analytics-table"><table><thead><tr><th>Métrica</th><th>Diferencia</th><th>Porcentaje</th><th>Denominador baseline</th></tr></thead><tbody>{Object.entries(d.deltas).map(([k,v])=><tr key={k}><td>{labels[k]??k}</td><td>{v.absolute ?? "No disponible"} {v.unit}</td><td>{v.percentage === null ? "No disponible":`${v.percentage}%`}</td><td>{v.denominator ?? "No disponible"}</td></tr>)}</tbody></table></Box><JsonDetails title="Alcance temporal" value={d.temporal_scope ?? "Metadato no registrado"} /></Box>)}
      </>}
      <JsonDetails title="Contexto común congelado, inventario, tarifas y versiones" value={comparison.context} /><Typography variant="caption">Contexto SHA-256: {comparison.context_sha256} · resultado: {comparison.result_sha256 ?? "pendiente"}</Typography>
    </Paper>}
    <Paper className="import-card"><Typography variant="h6">Historial de comparaciones</Typography>{history?.items.map(item=><Button className="history-button" key={item.comparison_id} onClick={()=>selectHistory(item.comparison_id)}>{item.comparison_id} · {item.status}</Button>)}<Typography>{history?.total ?? 0} comparaciones · desde {offset+1}</Typography><Button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-5))}>Comparaciones anteriores</Button><Button disabled={history?.next_offset==null} onClick={()=>setOffset(history!.next_offset!)}>Más comparaciones</Button></Paper>
  </Box></Box>;
}
