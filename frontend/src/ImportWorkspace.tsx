import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import {
  createScenario,
  getImport,
  getIssues,
  getPublication,
  getRevision,
  getValidation,
  listImports,
  listRevisions,
  listScenarios,
  lookupImport,
  publishImport,
  requestValidation,
  templateUrl,
  uploadImport,
  ImportApiError,
  type ImportBatch,
  type Revision,
  type RevisionDetail,
  type Scenario,
  type ValidationContext,
  type ValidationIssue,
  type ValidationState,
} from "./import-api";

const DATASETS = ["orders", "order_lines", "inventory", "distribution_centers", "vehicles"];
const DRAFT_KEY = "routeops.import-flow.v1";

type ContextFields = {
  planningDate: string;
  startTime: string;
  endTime: string;
  utcOffset: string;
  timezone: string;
  currency: string;
  areaWest: string;
  areaSouth: string;
  areaEast: string;
  areaNorth: string;
};

type SavedDraft = {
  scenarioId: string;
  batchId: string;
  uploadKey: string;
  context: ContextFields;
};

const today = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Santiago",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date());

const defaultContext: ContextFields = {
  planningDate: today,
  startTime: "08:00",
  endTime: "20:00",
  utcOffset: "-03:00",
  timezone: "America/Santiago",
  currency: "CLP",
  areaWest: "",
  areaSouth: "",
  areaEast: "",
  areaNorth: "",
};

function readDraft(): SavedDraft {
  try {
    const stored = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "null") as Partial<SavedDraft> | null;
    return {
      scenarioId: stored?.scenarioId ?? "",
      batchId: stored?.batchId ?? "",
      uploadKey: stored?.uploadKey ?? "",
      context: { ...defaultContext, ...stored?.context },
    };
  } catch {
    return { scenarioId: "", batchId: "", uploadKey: "", context: defaultContext };
  }
}

function errorText(reason: unknown): string {
  if (reason instanceof ImportApiError) {
    const explanations: Record<string, string> = {
      VALIDATION_CONTEXT_CONFLICT: "Este lote ya tiene otro contexto de validación. Carga un paquete nuevo.",
      BATCH_EXPIRED: "El lote expiró. Carga un paquete nuevo.",
      BATCH_NOT_VALID: "El lote todavía no cumple las condiciones de publicación.",
      PUBLISH_PACKAGE_EMPTY: "Un paquete completamente vacío no puede publicarse.",
      PUBLISH_INVENTORY_EMPTY: "El inventario vacío no aporta un snapshot_at de origen.",
      VALIDATED_SOURCE_MISMATCH: "Los originales ya no coinciden con el contenido validado.",
    };
    return `${explanations[reason.code] ?? reason.message} (${reason.code})`;
  }
  return reason instanceof Error ? reason.message : "No se pudo completar la operación.";
}

export function contextPayload(fields: ContextFields): ValidationContext {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(fields.planningDate)) {
    throw new Error("Indica una fecha de planificación válida.");
  }
  if (!/^[+-]\d{2}:\d{2}$/.test(fields.utcOffset)) {
    throw new Error("Indica un desfase UTC como -03:00 o +00:00.");
  }
  let area: Record<string, unknown> | null = null;
  const coordinates = [fields.areaWest, fields.areaSouth, fields.areaEast, fields.areaNorth];
  if (coordinates.some((value) => value.trim())) {
    if (coordinates.some((value) => !/^-?\d+(?:\.\d+)?$/.test(value.trim()))) {
      throw new Error("Completa los cuatro límites del área con números decimales.");
    }
    const [west, south, east, north] = coordinates.map(Number);
    if (west < -180 || east > 180 || south < -90 || north > 90 || west >= east || south >= north) {
      throw new Error("Comprueba que oeste < este y sur < norte, dentro de los rangos geográficos.");
    }
    area = { type: "MultiPolygon", coordinates: [[[[west, south], [east, south], [east, north], [west, north], [west, south]]]] };
  }
  return {
    planning_date: fields.planningDate,
    horizon_start_at: `${fields.planningDate}T${fields.startTime}:00${fields.utcOffset}`,
    horizon_end_at: `${fields.planningDate}T${fields.endTime}:00${fields.utcOffset}`,
    timezone_iana: fields.timezone.trim(),
    currency: fields.currency.trim().toUpperCase(),
    operational_area: area,
  };
}

export function acceptedFiles(files: File[]): boolean {
  if (files.length === 1) return files[0].name.toLowerCase().endsWith(".xlsx");
  if (files.length !== DATASETS.length) return false;
  const names = files.map((file) => file.name);
  return DATASETS.every((dataset) => names.includes(`${dataset}.csv`));
}

export function publicationBlocker(batch: ImportBatch | null, state: ValidationState | null): string | null {
  if (!batch) return "Primero carga un paquete.";
  const status = state?.status ?? batch.status;
  if (status === "EXPIRED") return "El lote expiró; carga un paquete nuevo.";
  if (status === "INVALID") return "La validación encontró errores. Revisa las incidencias y carga una versión corregida.";
  if (status === "FAILED") return "La validación falló tras sus reintentos. Carga un paquete nuevo.";
  if (status === "PUBLISHED") return "Este lote ya tiene una revisión publicada.";
  if (status !== "VALID") return "El lote debe terminar la validación antes de publicarse.";
  if (!state?.report?.valid) return "Falta un informe de validación válido.";
  const counts = state.report.counts;
  if (DATASETS.every((dataset) => (counts[dataset]?.accepted ?? 0) === 0)) {
    return "El paquete está completamente vacío.";
  }
  if ((counts.inventory?.accepted ?? 0) === 0) {
    return "El inventario está vacío; no existe un snapshot_at de origen.";
  }
  return null;
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <Paper className="import-card" elevation={0}>
      <Typography variant="h6" sx={{ fontWeight: 750 }}>{title}</Typography>
      {note && <Typography color="text.secondary" variant="body2">{note}</Typography>}
      <Divider sx={{ my: 2 }} />
      {children}
    </Paper>
  );
}

export function ImportWorkspace() {
  const initial = useMemo(readDraft, []);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioOffset, setScenarioOffset] = useState<number | null>(null);
  const [scenarioId, setScenarioId] = useState(initial.scenarioId);
  const [scenarioName, setScenarioName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [batchId, setBatchId] = useState(initial.batchId);
  const [uploadKey, setUploadKey] = useState(initial.uploadKey);
  const [context, setContext] = useState(initial.context);
  const [validation, setValidation] = useState<ValidationState | null>(null);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [issueAfter, setIssueAfter] = useState(0);
  const [nextIssueAfter, setNextIssueAfter] = useState<number | null>(null);
  const [imports, setImports] = useState<ImportBatch[]>([]);
  const [importOffset, setImportOffset] = useState<number | null>(null);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [revisionAfter, setRevisionAfter] = useState<number | null>(null);
  const [selectedRevision, setSelectedRevision] = useState<RevisionDetail | null>(null);
  const [publication, setPublication] = useState<Revision | null>(null);
  const [busy, setBusy] = useState<"scenario" | "upload" | "validation" | "publication" | null>(null);
  const [notice, setNotice] = useState<{ severity: "error" | "success" | "info"; text: string } | null>(null);
  const operation = useRef(false);

  const remember = useCallback((next: Partial<SavedDraft>) => {
    const current = readDraft();
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...current, ...next }));
  }, []);

  const loadScenarios = useCallback(async (offset = 0) => {
    const page = await listScenarios(offset);
    setScenarios((current) => offset === 0 ? page.items : [...current, ...page.items]);
    setScenarioOffset(page.next_offset);
  }, []);

  const loadHistory = useCallback(async (currentScenario: string, after = 0) => {
    const page = await listRevisions(currentScenario, after);
    setRevisions((current) => after === 0 ? page.items : [...current, ...page.items]);
    setRevisionAfter(page.next_after);
  }, []);

  const loadImports = useCallback(async (currentScenario: string, offset = 0) => {
    const page = await listImports(currentScenario, offset);
    setImports((current) => offset === 0 ? page.items : [...current, ...page.items]);
    setImportOffset(page.next_offset);
  }, []);

  const loadIssues = useCallback(async (currentScenario: string, currentBatch: string, after = 0) => {
    const page = await getIssues(currentScenario, currentBatch, after);
    setIssues(page.items);
    setIssueAfter(after);
    setNextIssueAfter(page.next_after);
  }, []);

  const recoverBatch = useCallback(async (currentScenario: string, currentBatch: string) => {
    const [loadedBatch, loadedValidation] = await Promise.all([
      getImport(currentScenario, currentBatch),
      getValidation(currentScenario, currentBatch),
    ]);
    setBatch(loadedBatch);
    setBatchId(currentBatch);
    setValidation(loadedValidation);
    remember({ scenarioId: currentScenario, batchId: currentBatch });
    if (loadedValidation.report) await loadIssues(currentScenario, currentBatch);
    else { setIssues([]); setNextIssueAfter(null); }
    if (loadedBatch.status === "PUBLISHED") {
      setPublication(await getPublication(currentScenario, currentBatch));
    } else setPublication(null);
  }, [loadIssues, remember]);

  useEffect(() => {
    loadScenarios().catch((reason) => setNotice({ severity: "error", text: errorText(reason) }));
  }, [loadScenarios]);

  useEffect(() => {
    if (!scenarioId) return;
    loadImports(scenarioId).catch((reason) => setNotice({ severity: "error", text: errorText(reason) }));
    loadHistory(scenarioId).catch((reason) => setNotice({ severity: "error", text: errorText(reason) }));
    if (batchId) {
      recoverBatch(scenarioId, batchId).catch((reason) =>
        setNotice({ severity: "error", text: errorText(reason) }));
    } else if (uploadKey) {
      lookupImport(scenarioId, uploadKey)
        .then((found) => recoverBatch(scenarioId, found.id))
        .catch((reason) => {
          if (reason instanceof Error && "status" in reason && reason.status === 404) {
            setNotice({ severity: "info", text: "La carga anterior no quedó registrada. Selecciona los mismos archivos para reintentar." });
          } else setNotice({ severity: "error", text: errorText(reason) });
        });
    }
    // Selection changes start a new recovery cycle; status polling uses a separate effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenarioId]);

  useEffect(() => {
    if (!scenarioId || !batchId || validation?.status !== "VALIDATING") return;
    const timer = window.setInterval(() => {
      recoverBatch(scenarioId, batchId).catch((reason) =>
        setNotice({ severity: "error", text: errorText(reason) }));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [scenarioId, batchId, validation?.status, recoverBatch]);

  useEffect(() => { remember({ scenarioId, batchId, uploadKey, context }); }, [scenarioId, batchId, uploadKey, context, remember]);

  const selectScenario = (nextId: string) => {
    setScenarioId(nextId);
    setBatch(null);
    setBatchId("");
    setUploadKey("");
    setValidation(null);
    setPublication(null);
    setIssues([]);
    setSelectedRevision(null);
    remember({ scenarioId: nextId, batchId: "", uploadKey: "" });
  };

  const create = async () => {
    if (operation.current || !scenarioName.trim()) return;
    operation.current = true;
    setBusy("scenario");
    try {
      const created = await createScenario(scenarioName.trim());
      await loadScenarios();
      selectScenario(created.id);
      setScenarioName("");
      setNotice({ severity: "success", text: "Escenario creado." });
    } catch (reason) { setNotice({ severity: "error", text: errorText(reason) }); }
    finally { operation.current = false; setBusy(null); }
  };

  const newUpload = () => {
    setBatch(null);
    setBatchId("");
    setUploadKey("");
    setFiles([]);
    setValidation(null);
    setPublication(null);
    setIssues([]);
    setNotice(null);
    remember({ batchId: "", uploadKey: "" });
  };

  const upload = async () => {
    if (operation.current || !scenarioId || !acceptedFiles(files) || batch) return;
    operation.current = true;
    setBusy("upload");
    const key = uploadKey || crypto.randomUUID();
    setUploadKey(key);
    remember({ scenarioId, batchId: "", uploadKey: key });
    try {
      const uploaded = await uploadImport(scenarioId, files, key);
      await recoverBatch(scenarioId, uploaded.id);
      await loadImports(scenarioId);
      setFiles([]);
      setNotice({ severity: "success", text: "Carga provisional recibida. Falta validar y publicar." });
    } catch (reason) {
      try {
        const found = await lookupImport(scenarioId, key);
        await recoverBatch(scenarioId, found.id);
        setNotice({ severity: "info", text: "Se recuperó una carga confirmada cuya respuesta se perdió." });
      } catch {
        setNotice({ severity: "error", text: `${errorText(reason)} Puedes reintentar con los mismos archivos y la misma clave.` });
      }
    } finally { operation.current = false; setBusy(null); }
  };

  const validate = async () => {
    if (operation.current || !scenarioId || !batchId) return;
    operation.current = true;
    setBusy("validation");
    try {
      const payload = contextPayload(context);
      remember({ context });
      const result = await requestValidation(scenarioId, batchId, payload);
      setValidation(result);
      await recoverBatch(scenarioId, batchId);
      setNotice({ severity: "info", text: "Validación solicitada. El trabajo se recupera automáticamente tras interrupciones." });
    } catch (reason) {
      await recoverBatch(scenarioId, batchId).catch(() => undefined);
      setNotice({ severity: "error", text: errorText(reason) });
    } finally { operation.current = false; setBusy(null); }
  };

  const publish = async () => {
    if (operation.current || !scenarioId || !batchId || (publicationBlocker(batch, validation) && batch?.status !== "PUBLISHED")) return;
    operation.current = true;
    setBusy("publication");
    try {
      const result = await publishImport(scenarioId, batchId);
      setPublication(result);
      await recoverBatch(scenarioId, batchId);
      await loadHistory(scenarioId);
      await loadImports(scenarioId);
      setNotice({ severity: "success", text: `Revisión ${result.revision_no} publicada.` });
    } catch (reason) {
      try {
        const result = await getPublication(scenarioId, batchId);
        setPublication(result);
        await recoverBatch(scenarioId, batchId);
        await loadHistory(scenarioId);
        await loadImports(scenarioId);
        setNotice({ severity: "info", text: `Se recuperó la revisión ${result.revision_no} ya publicada.` });
      } catch {
        await recoverBatch(scenarioId, batchId).catch(() => undefined);
        setNotice({ severity: "error", text: errorText(reason) });
      }
    } finally { operation.current = false; setBusy(null); }
  };

  const blocker = publicationBlocker(batch, validation);
  const activeStatus = validation?.status ?? batch?.status;

  return (
    <Box className="shell">
      <Box component="header" className="topbar">
        <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
          <Box className="brand-mark">R</Box>
          <Box><Typography sx={{ fontWeight: 800 }}>RouteOps</Typography><Typography variant="caption" color="text.secondary">Importaciones</Typography></Box>
        </Stack>
        <Stack direction="row" spacing={1}>
          <Button href="/">Tablero demo</Button>
          <Button href="/planning">Planificación</Button>
          <Chip label="Acceso local" size="small" color="primary" variant="outlined" />
        </Stack>
      </Box>
      <Box component="main" className="content import-content">
        <Box className="headline">
          <Box>
            <Typography variant="overline" color="primary.main">Hito 2 · Importaciones</Typography>
            <Typography variant="h4">Preparar un escenario</Typography>
            <Typography color="text.secondary">Carga datos privados, revisa incidencias y publica una revisión inmutable.</Typography>
          </Box>
        </Box>
        {notice && <Alert severity={notice.severity} onClose={() => setNotice(null)} sx={{ mb: 2 }}>{notice.text}</Alert>}
        <Box className="import-grid">
          <Stack spacing={2}>
            <Section title="1. Escenario" note="Crea uno o continúa con un escenario existente.">
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
                <TextField select label="Escenario" value={scenarioId} onChange={(event) => selectScenario(event.target.value)} fullWidth slotProps={{ inputLabel: { shrink: true }, select: { native: true } }}>
                  <option value="">Selecciona un escenario</option>
                  {scenarios.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.status}</option>)}
                </TextField>
                {scenarioOffset !== null && <Button onClick={() => loadScenarios(scenarioOffset)}>Más</Button>}
              </Stack>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mt: 2 }}>
                <TextField label="Nuevo escenario" value={scenarioName} onChange={(event) => setScenarioName(event.target.value)} fullWidth slotProps={{ htmlInput: { maxLength: 200 } }} />
                <Button variant="outlined" disabled={busy !== null || !scenarioName.trim()} onClick={create}>{busy === "scenario" ? "Creando…" : "Crear"}</Button>
              </Stack>
            </Section>

            <Section title="2. Plantillas y carga" note="Descarga el contrato y selecciona cinco CSV con nombres exactos, o un XLSX con las cinco hojas.">
              <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1, mb: 2 }}>
                {DATASETS.map((name) => <Button key={name} size="small" component="a" href={templateUrl(name)} download>{name}.csv</Button>)}
                <Button size="small" component="a" href={templateUrl("workbook")} download>XLSX</Button>
              </Stack>
              {batch ? (
                <Stack spacing={1}>
                  <Typography variant="body2">Lote <strong>{batch.id}</strong></Typography>
                  <Chip label={activeStatus ?? batch.status} color={activeStatus === "PUBLISHED" ? "success" : activeStatus === "INVALID" || activeStatus === "EXPIRED" ? "error" : "info"} sx={{ alignSelf: "flex-start" }} />
                  <Typography variant="caption" color="text.secondary">{batch.files.map((file) => file.display_name).join(", ")}</Typography>
                  {batch.expires_at && <Typography variant="caption" color="text.secondary">Expira: {new Date(batch.expires_at).toLocaleString()}</Typography>}
                  <Button size="small" onClick={newUpload} sx={{ alignSelf: "flex-start" }}>Nueva carga</Button>
                </Stack>
              ) : (
                <Stack spacing={1}>
                  <Button variant="outlined" component="label" disabled={!scenarioId || busy !== null} sx={{ alignSelf: "flex-start" }}>
                    Seleccionar archivos
                    <input hidden type="file" multiple accept=".csv,.xlsx" onChange={(event) => setFiles(Array.from(event.target.files ?? []))} />
                  </Button>
                  <Typography variant="body2" color={files.length && !acceptedFiles(files) ? "error.main" : "text.secondary"}>
                    {files.length ? files.map((file) => file.name).join(", ") : "Ningún archivo seleccionado"}
                  </Typography>
                  {uploadKey && <Typography variant="caption" color="text.secondary">Reintento pendiente: selecciona el mismo paquete.</Typography>}
                  <Button variant="contained" disabled={!scenarioId || !acceptedFiles(files) || busy !== null} onClick={upload} sx={{ alignSelf: "flex-start" }}>
                    {busy === "upload" ? "Recibiendo…" : "Cargar paquete"}
                  </Button>
                </Stack>
              )}
            </Section>

            <Section title="3. Contexto y validación" note="Los instantes se envían con desfase UTC explícito; los horarios locales se interpretan en la zona IANA.">
              <Box className="context-grid">
                <TextField label="Fecha de planificación" type="date" value={context.planningDate} onChange={(event) => setContext({ ...context, planningDate: event.target.value })} slotProps={{ inputLabel: { shrink: true } }} />
                <TextField label="Inicio" type="time" value={context.startTime} onChange={(event) => setContext({ ...context, startTime: event.target.value })} slotProps={{ inputLabel: { shrink: true } }} />
                <TextField label="Fin" type="time" value={context.endTime} onChange={(event) => setContext({ ...context, endTime: event.target.value })} slotProps={{ inputLabel: { shrink: true } }} />
                <TextField label="Desfase UTC" value={context.utcOffset} onChange={(event) => setContext({ ...context, utcOffset: event.target.value })} helperText="Ejemplo: -03:00" />
                <TextField label="Zona IANA" value={context.timezone} onChange={(event) => setContext({ ...context, timezone: event.target.value })} />
                <TextField label="Moneda" value={context.currency} onChange={(event) => setContext({ ...context, currency: event.target.value.toUpperCase() })} slotProps={{ htmlInput: { maxLength: 3 } }} />
              </Box>
              <Typography variant="subtitle2" sx={{ mt: 2, mb: 1 }}>Área operativa opcional · rectángulo</Typography>
              <Box className="context-grid">
                <TextField label="Oeste (longitud)" value={context.areaWest} onChange={(event) => setContext({ ...context, areaWest: event.target.value })} placeholder="-70.8" />
                <TextField label="Sur (latitud)" value={context.areaSouth} onChange={(event) => setContext({ ...context, areaSouth: event.target.value })} placeholder="-33.7" />
                <TextField label="Este (longitud)" value={context.areaEast} onChange={(event) => setContext({ ...context, areaEast: event.target.value })} placeholder="-70.3" />
                <TextField label="Norte (latitud)" value={context.areaNorth} onChange={(event) => setContext({ ...context, areaNorth: event.target.value })} placeholder="-33.2" />
              </Box>
              <Typography color="text.secondary" variant="caption">Completa los cuatro límites o déjalos vacíos. Los puntos sobre el borde se consideran cubiertos.</Typography>
              <Stack direction="row" spacing={1} sx={{ alignItems: "center", mt: 2 }}>
                <Button variant="contained" disabled={!batch || activeStatus !== "RECEIVED" || busy !== null} onClick={validate}>Solicitar validación</Button>
                {activeStatus === "VALIDATING" && <><CircularProgress size={18} /><Typography variant="body2">Validando · intento {validation?.attempts ?? 0}/{validation?.max_attempts ?? 3}</Typography></>}
                {batch && <Button size="small" onClick={() => recoverBatch(scenarioId, batch.id)}>Actualizar estado</Button>}
              </Stack>
              {validation?.report && (
                <Stack spacing={2} sx={{ mt: 2 }}>
                  <Alert severity={validation.report.valid ? "success" : "error"}>
                    {validation.report.valid ? activeStatus === "PUBLISHED" ? "Validación conservada en la revisión publicada." : "Lote validado. Todavía no es una revisión publicada." : "Lote inválido. Revisa las incidencias."}
                  </Alert>
                  <Box className="count-grid">
                    {DATASETS.map((name) => <Box key={name} className="count-cell"><Typography sx={{ fontWeight: 700 }}>{name}</Typography><Typography variant="body2">{validation.report?.counts[name]?.accepted ?? 0} aceptadas · {validation.report?.counts[name]?.rejected ?? 0} rechazadas</Typography></Box>)}
                  </Box>
                  <Box><Typography sx={{ fontWeight: 700 }}>Comprobado</Typography><Typography variant="body2" color="text.secondary">{validation.report.checked_rules.join(" · ")}</Typography></Box>
                  <Box><Typography sx={{ fontWeight: 700 }}>Pendiente de otras fases</Typography><Typography variant="body2" color="text.secondary">{validation.report.deferred_rules.join(" · ")}</Typography></Box>
                  <Typography variant="subtitle2">Incidencias</Typography>
                  {issues.length === 0 ? <Typography color="text.secondary" variant="body2">Sin incidencias en esta página.</Typography> : (
                    <Box className="issue-list">
                      {issues.map((issue) => <Box key={issue.ordinal} className="issue-row">
                        <Chip size="small" color={issue.severity === "ERROR" ? "error" : "warning"} label={issue.severity} />
                        <Box><Typography variant="body2" sx={{ fontWeight: 700 }}>{issue.code} · {issue.dataset ?? "paquete"} · fila {issue.row ?? "—"} · {issue.field ?? "—"}</Typography><Typography variant="body2" color="text.secondary">{issue.message}</Typography></Box>
                      </Box>)}
                    </Box>
                  )}
                  <Stack direction="row" spacing={1}>
                    {issueAfter > 0 && <Button size="small" onClick={() => loadIssues(scenarioId, batchId)}>Primera página</Button>}
                    {nextIssueAfter !== null && <Button size="small" onClick={() => loadIssues(scenarioId, batchId, nextIssueAfter)}>Más incidencias</Button>}
                  </Stack>
                </Stack>
              )}
            </Section>

            <Section title="4. Publicación" note="La API vuelve a comprobar originales, contexto, informe, vigencia y restricciones antes de confirmar.">
              {publication ? <Alert severity="success">Revisión {publication.revision_no} publicada · {publication.id}</Alert> : <Alert severity={blocker ? "info" : "success"}>{blocker ?? "El lote validado puede publicarse."}</Alert>}
              <Button variant="contained" sx={{ mt: 2 }} disabled={busy !== null || (blocker !== null && activeStatus !== "PUBLISHED")} onClick={publish}>{busy === "publication" ? "Publicando…" : activeStatus === "PUBLISHED" ? "Reintentar publicación" : "Publicar revisión"}</Button>
            </Section>
          </Stack>

          <Stack spacing={2}>
            <Section title="Lotes recientes" note="Selecciona un lote para retomar su estado después de recargar.">
              <Stack spacing={1}>
                {imports.map((item) => <Button key={item.id} variant={item.id === batchId ? "contained" : "text"} className="history-button" onClick={() => recoverBatch(scenarioId, item.id)}>{item.status} · {item.id.slice(0, 8)} · {new Date(item.created_at).toLocaleString()}</Button>)}
                {imports.length === 0 && <Typography variant="body2" color="text.secondary">Aún no hay lotes.</Typography>}
                {importOffset !== null && <Button onClick={() => loadImports(scenarioId, importOffset)}>Más lotes</Button>}
              </Stack>
            </Section>
            <Section title="Revisiones publicadas" note="Cada publicación conserva sus datos y procedencia.">
              <Stack spacing={1}>
                {revisions.map((item) => <Button key={item.id} variant={selectedRevision?.id === item.id ? "contained" : "text"} className="history-button" onClick={() => getRevision(scenarioId, item.revision_no).then(setSelectedRevision).catch((reason) => setNotice({ severity: "error", text: errorText(reason) }))}>Revisión {item.revision_no} · {item.planning_date}</Button>)}
                {revisions.length === 0 && <Typography variant="body2" color="text.secondary">Todavía no hay revisiones publicadas.</Typography>}
                {revisionAfter !== null && <Button onClick={() => loadHistory(scenarioId, revisionAfter)}>Más revisiones</Button>}
              </Stack>
              {selectedRevision && <Box sx={{ mt: 2 }} className="revision-detail">
                <Typography sx={{ fontWeight: 700 }}>Revisión {selectedRevision.revision_no}</Typography>
                <Typography variant="body2">Lote: {selectedRevision.import_batch_id}</Typography>
                <Typography variant="body2">Publicada: {new Date(selectedRevision.published_at).toLocaleString()}</Typography>
                <Typography variant="body2">Snapshot: {selectedRevision.snapshot_at ? new Date(selectedRevision.snapshot_at).toLocaleString() : "No disponible"}</Typography>
                <Typography variant="body2">Zona: {selectedRevision.timezone_iana} · Moneda: {selectedRevision.currency}</Typography>
                {selectedRevision.counts && DATASETS.map((name) => <Typography key={name} variant="caption" sx={{ display: "block" }}>{name}: {selectedRevision.counts?.[name]?.accepted ?? 0}</Typography>)}
              </Box>}
            </Section>
          </Stack>
        </Box>
      </Box>
    </Box>
  );
}
