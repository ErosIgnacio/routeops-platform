export function formatEstimatedCost(value: number, currency: string): string {
  const amount = new Intl.NumberFormat("es-CL", {
    maximumFractionDigits: 0,
    minimumFractionDigits: 0,
  }).format(value);
  return currency === "CLP" ? `CLP $${amount}` : `${currency} ${amount}`;
}
