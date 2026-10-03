import { useEffect, useState } from "react";
import { Alert, Box, Button, MenuItem, Paper, TextField, Typography } from "@mui/material";
import { allScenarios, analyticsRequest } from "./analytics-api";
import { AnalyticsPanel } from "./AnalyticsPanel";
import type { Scenario } from "./import-api";
import type { RevisionRun } from "./planning-api";
import type { PlanningRun } from "./types";
import { RouteMap } from "./RouteMap";
import { WorkspaceNav } from "./WorkspaceNav";

export function AnalyticsWorkspace() {
  const [scenarios,setScenarios] = useState<Scenario[]>([]), [scenario,setScenario] = useState("");
  const [page,setPage] = useState<{items:RevisionRun[];next_offset:number|null}>({items:[],next_offset:null}), [offset,setOffset] = useState(0);
  const [id,setId] = useState(()=>new URLSearchParams(location.search).get("run") ?? localStorage.getItem("routeops.analytics.run") ?? "");
  const [enteredId,setEnteredId] = useState(id), [vehicle,setVehicle] = useState<string|null>(null);
  const [run,setRun] = useState<PlanningRun | null>(null), [error,setError] = useState("");
  useEffect(()=>{allScenarios().then(setScenarios).catch(e=>setError(String(e)));},[]);
  useEffect(()=>{let live=true;setPage({items:[],next_offset:null});if(scenario)analyticsRequest<{items:RevisionRun[];next_offset:number|null}>(`/scenarios/${scenario}/revision-runs?limit=10&offset=${offset}`).then(p=>{if(live)setPage(p);}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[scenario,offset]);
  useEffect(()=>{setRun(null);setVehicle(null);setError("");if(!id)return;localStorage.setItem("routeops.analytics.run",id);let live=true;let timer:ReturnType<typeof setTimeout>|undefined;const read=async()=>{try{const value=await analyticsRequest<PlanningRun>(`/runs/${id}`);if(live){setRun(value);if(["QUEUED","RUNNING"].includes(value.status))timer=setTimeout(read,1000);}}catch(e){if(live)setError(String(e));}};void read();return()=>{live=false;if(timer)clearTimeout(timer);};},[id]);
  const selectRun = (value:string) => {setEnteredId(value);setId(value);};
  return <Box className="shell"><WorkspaceNav title="Analítica de operación"/><Box component="main" className="content import-content">
    <Typography variant="h4">Planes, KPIs y diagnósticos</Typography>{error&&<Alert severity="error">{error}</Alert>}
    <Paper className="import-card"><TextField select label="Escenario para consultar corridas" value={scenario} onChange={e=>{setScenario(e.target.value);setOffset(0);}} fullWidth>{scenarios.map(s=><MenuItem key={s.id} value={s.id}>{s.name} · {s.id.slice(0,8)}</MenuItem>)}</TextField>
      {page.items.map(r=><Button className="history-button" key={r.run_id} onClick={()=>selectRun(r.run_id)}>{r.run_id} · {r.status}</Button>)}
      {scenario&&<Box><Typography>Historial desde {offset+1}</Typography><Button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-10))}>Corridas anteriores</Button><Button disabled={page.next_offset===null} onClick={()=>setOffset(page.next_offset!)}>Más corridas</Button></Box>}
      <TextField label="Identificador de corrida (incluye históricas)" value={enteredId} onChange={e=>setEnteredId(e.target.value)} fullWidth sx={{mt:2}}/>
      <Button disabled={!enteredId} onClick={()=>setId(enteredId)}>Consultar corrida</Button>
    </Paper>
    {run&&<><Typography>{run.run_id} · {run.status}</Typography><AnalyticsPanel runId={run.run_id} status={run.status} selectedVehicleId={vehicle} onSelectRoute={setVehicle}/><Paper className="map-panel"><Box className="panel-heading">Mapa del plan estimado · {run.status}</Box><RouteMap run={run} selectedVehicleId={vehicle} onShowAll={()=>setVehicle(null)}/></Paper><Button href="/planning">Revisar aceptación y cancelación en Planificación</Button></>}
  </Box></Box>;
}
