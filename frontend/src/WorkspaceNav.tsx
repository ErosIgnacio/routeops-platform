import { Box, Button, Stack, Typography } from "@mui/material";
export function WorkspaceNav({title}: {title: string}) {
  return <Box component="header" className="topbar"><Typography sx={{fontWeight:800}}>RouteOps · {title}</Typography><Stack direction="row" spacing={1} sx={{flexWrap:"wrap"}}><Button href="/">Tablero</Button><Button href="/imports">Importaciones</Button><Button href="/planning">Planificación</Button><Button href="/analytics">Analítica</Button><Button href="/comparisons">Comparaciones</Button></Stack></Box>;
}
