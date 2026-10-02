import { Box, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";

import type { OptimizedRoute } from "./types";

export function RouteSequence({ routes, finished = false }: { routes: OptimizedRoute[]; finished?: boolean }) {
  const stops = routes.flatMap((route) =>
    route.steps.map((step) => ({
      key: `${route.vehicle_id}-${step.sequence}`,
      vehicle: route.source_vehicle_id,
      sequence: step.sequence,
      stop: step.order_id ?? step.kind,
      kind: step.kind,
    })),
  );

  if (stops.length === 0) {
    return (
      <Typography className="empty-state" color="text.secondary">
        {finished ? "La corrida terminó sin rutas. Consulta las excepciones y reservas." : "Solicita una corrida para consultar la secuencia de paradas."}
      </Typography>
    );
  }

  return (
    <Box className="scroll-list compact" sx={{ overflowX: "auto" }}>
      <Table size="small" aria-label="Persisted route stop sequence">
        <TableHead>
          <TableRow>
            <TableCell>Vehicle</TableCell>
            <TableCell align="right">Seq.</TableCell>
            <TableCell>Stop</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {stops.map((stop) => (
            <TableRow key={stop.key} data-kind={stop.kind}>
              <TableCell>{stop.vehicle}</TableCell>
              <TableCell align="right">{stop.sequence}</TableCell>
              <TableCell>{stop.stop}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}
