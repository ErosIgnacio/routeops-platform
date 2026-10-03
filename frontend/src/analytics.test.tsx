import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const api = vi.hoisted(()=>({allScenarios:vi.fn(),analyticsRequest:vi.fn(),getRunAnalytics:vi.fn(),getDiagnostics:vi.fn(),comparisonHistory:vi.fn(),comparisonInput:vi.fn(),createComparison:vi.fn(),getComparison:vi.fn()}));
const planning = vi.hoisted(()=>({listScenarios:vi.fn(),listRevisions:vi.fn()}));
vi.mock("./analytics-api",()=>api);
vi.mock("./planning-api",()=>planning);
vi.mock("./RouteMap",()=>({RouteMap:()=> <div>Mapa del plan seleccionado</div>}));
import { AnalyticsPanel, MetricGrid, RouteFacts } from "./AnalyticsPanel";
import { AnalyticsWorkspace } from "./AnalyticsWorkspace";
import { ComparisonWorkspace } from "./ComparisonWorkspace";

const metric = (value: string | number | null, unit="ratio")=>({value,unit,denominator:2,unavailable_reason:value===null?"UNKNOWN":"",calculation_version:"v1",provenance:"persisted"});
const plan = {feasible:false,routes:[],departure_policy:"EFFECTIVE_SHIFT_START",metrics:{metrics:{coverage:metric("0"),operating_cost:metric(null,"CLP")},routes:[],solver_objective:null},incidences:[{code:"MANUAL_STOCK_NO_FULL_COVERAGE",stage:"MANUAL_EVALUATION",certainty:"PROVEN",detail:"Stock insuficiente",evidence:{scope:"frozen_inventory"}}],decisions:[],solver_objective:null};
const comparison = {comparison_id:"comparison-1",status:"READY",attempts:1,scenario_id:"s",history:[],context_sha256:"hash",context:{simulation_only:true},result_sha256:"result",manual_input:{manual_routes:[],solution_quality:"BALANCED",revision_no:1},result:{alternatives:{manual:plan,"greedy-v1":{...plan,feasible:true,departure_policy:"SOLVER_CHOSEN_WITHIN_EFFECTIVE_SHIFT"}},comparisons:{"manual__greedy-v1":{comparability:"INFEASIBLE_PLAN",scope:"complete_input_set",savings_claim_allowed:false,deltas:{operating_cost:{absolute:null,percentage:null,unit:"CLP",denominator:null}}}}}};
beforeEach(()=>{vi.clearAllMocks();localStorage.clear();api.allScenarios.mockResolvedValue([{id:"s",name:"Escenario aislado"}]);planning.listRevisions.mockResolvedValue({items:[{id:"r",revision_no:1}]});api.comparisonInput.mockResolvedValue({orders:["001","002"],centers:["CD"],timezone:"America/Santiago",vehicles:[{vehicle_id:"V",center_id:"CD",shift_start:"2026-10-15T08:00:00-03:00"}]});api.comparisonHistory.mockResolvedValue({items:[{comparison_id:"comparison-1",status:"READY"}],total:1,next_offset:null});api.getComparison.mockResolvedValue(comparison);});
afterEach(cleanup);
describe("authoritative analytics",()=>{
  it("selects a route through the shared map callback",()=>{
    const onSelect=vi.fn();render(<RouteFacts routes={[{source_vehicle_id:"V",distribution_center_id:"CD",steps:[]} as never]} metrics={{routes:[]} as never} onSelect={onSelect} selected={null}/>);
    fireEvent.click(screen.getByRole("button",{name:"V · CD"}));expect(onSelect).toHaveBeenCalledWith("V");
  });
  it("loads an entered historical ID only after consultation and preserves server history pagination",async()=>{
    api.analyticsRequest.mockImplementation((path:string)=>Promise.resolve(path.startsWith("/runs/")?{run_id:"historical",status:"ACCEPTED",result:null}:{items:[],next_offset:10}));
    api.getRunAnalytics.mockResolvedValue({run:{input:{},result:null},metrics:{current_status:"ACCEPTED",plan:null,processing:{},current_reservations:[]}});api.getDiagnostics.mockResolvedValue({items:[],total:0,next_offset:null,provenance:{}});
    render(<AnalyticsWorkspace/>);fireEvent.change(screen.getByLabelText("Identificador de corrida (incluye históricas)"),{target:{value:"historical"}});expect(api.analyticsRequest).not.toHaveBeenCalledWith("/runs/historical");
    fireEvent.click(screen.getByRole("button",{name:"Consultar corrida"}));await screen.findByText("historical · ACCEPTED");expect(localStorage.getItem("routeops.analytics.run")).toBe("historical");
    fireEvent.mouseDown(screen.getByRole("combobox",{name:"Escenario para consultar corridas"}));fireEvent.click(await screen.findByRole("option",{name:/Escenario aislado/}));await screen.findByText("Historial desde 1");fireEvent.click(screen.getByRole("button",{name:"Más corridas"}));await waitFor(()=>expect(api.analyticsRequest).toHaveBeenCalledWith("/scenarios/s/revision-runs?limit=10&offset=10"));
  });
  it("keeps zero distinct from unavailable and shows units and denominators",()=>{render(<MetricGrid metrics={{coverage:metric(0),operating_cost:metric(null,"CLP")}}/>);expect(screen.getByText("0 ratio")).toBeTruthy();expect(screen.getByText(/No disponible · UNKNOWN/)).toBeTruthy();expect(screen.getAllByText(/Denominador: 2/)).toHaveLength(2);});
  it("labels canceled routes as historical with RELEASED and partial measurements",async()=>{
    api.getRunAnalytics.mockResolvedValue({run:{input:{},result:null},metrics:{current_status:"CANCELED",plan:null,processing:{total_elapsed:{value:"12",classification:"PARTIAL"},durable_total_elapsed:{value:null,classification:"UNKNOWN"}},current_reservations:[{order_id:"001",status:"RELEASED"}]}});
    api.getDiagnostics.mockResolvedValue({items:[],total:0,next_offset:null,provenance:{},unavailable_reason:null});render(<AnalyticsPanel runId="r" status="CANCELED"/>);
    expect(await screen.findByText("001 · RELEASED")).toBeTruthy();expect(screen.getByText(/Plan histórico cancelado/)).toBeTruthy();expect(screen.getByText(/acuse durable es desconocido/)).toBeTruthy();
    expect(screen.getByRole("link",{name:"Descargar XLSX"}).getAttribute("href")).toBe("/api/v1/runs/r/exports/xlsx");
  });
  it("paginates diagnostics with the backend and clears the page for a filter",async()=>{
    api.getRunAnalytics.mockResolvedValue({run:{input:{},result:null},metrics:{current_status:"READY",plan:null,processing:{},current_reservations:[]}});
    api.getDiagnostics.mockResolvedValue({items:[],total:6,next_offset:5,provenance:{},unavailable_reason:null});render(<AnalyticsPanel runId="r" status="READY"/>);
    await screen.findByText(/6 diagnósticos/);fireEvent.click(screen.getByRole("button",{name:"Siguientes diagnósticos"}));await waitFor(()=>expect(api.getDiagnostics).toHaveBeenLastCalledWith("r",5,"","",""));
    fireEvent.change(screen.getByLabelText("Código exacto"),{target:{value:"STOCK_NO_FULL_COVERAGE"}});await waitFor(()=>expect(api.getDiagnostics).toHaveBeenLastCalledWith("r",0,"","","STOCK_NO_FULL_COVERAGE"));
  });
});
describe("comparison recovery and feasibility",()=>{
  const draft={scenario:"s",revision:1,routes:[{vehicle_id:"V",center_id:"CD",order_ids:["001"]}],key:"",id:"",quality:"BALANCED"};
  it("preserves the exact request and key after response loss and restores the same result",async()=>{
    localStorage.setItem("routeops.comparison.v1",JSON.stringify(draft));api.createComparison.mockRejectedValueOnce(new Error("response lost")).mockResolvedValue(comparison);
    render(<ComparisonWorkspace/>);await screen.findByText(/Pendientes: 002/);fireEvent.click(screen.getByRole("button",{name:"Crear comparación"}));await screen.findByText("response lost");
    const sent=api.createComparison.mock.calls[0];expect(JSON.parse(localStorage.getItem("routeops.comparison.v1")!).key).toBe(sent[2]);
    fireEvent.click(screen.getByRole("button",{name:"Recuperar misma solicitud"}));await screen.findByText("Comparación comparison-1");expect(api.createComparison.mock.calls[1]).toEqual(sent);expect(screen.getByText(/Plan inviable: métricas solo informativas/)).toBeTruthy();expect(screen.getByText("No corresponde afirmar ahorro comparable.")).toBeTruthy();
    cleanup();render(<ComparisonWorkspace/>);await screen.findByText("Comparación comparison-1");expect(api.getComparison).toHaveBeenCalledWith("comparison-1");expect(api.createComparison).toHaveBeenCalledTimes(2);
  });
  it("shows duplicate and unknown IDs without rewriting the supplied sequence",async()=>{
    localStorage.setItem("routeops.comparison.v1",JSON.stringify({...draft,routes:[{vehicle_id:"V",center_id:"CD",order_ids:["001","001","X"]}]}));render(<ComparisonWorkspace/>);
    await screen.findByText("Duplicados: 001");expect(screen.getByText("Desconocidos: X")).toBeTruthy();expect((screen.getByLabelText("Secuencia ruta 1") as HTMLTextAreaElement).value).toBe("001\n001\nX");
  });
  it("selects history and changes plan without issuing an operational action",async()=>{
    localStorage.setItem("routeops.comparison.v1",JSON.stringify({...draft,id:"comparison-1"}));render(<ComparisonWorkspace/>);await screen.findByText("Comparación comparison-1");expect(api.createComparison).not.toHaveBeenCalled();expect(screen.getByText(/comparar no reserva ni consume/)).toBeTruthy();expect(screen.getByText(/EFFECTIVE_SHIFT_START/)).toBeTruthy();
  });
});
