import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pulso TransMi · Observabilidad",
  description: "Accuracy, drift, cobertura y salud del pipeline de predicción."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="es"><body>{children}</body></html>;
}
