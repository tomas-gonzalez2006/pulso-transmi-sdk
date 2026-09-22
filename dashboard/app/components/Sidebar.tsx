import Link from "next/link";

export default function Sidebar() {
  return <aside className="sidebar"><div className="brand"><span className="brand-mark">P</span><span>Pulso<br /><small>TransMi MLOps</small></span></div><nav><Link href="/">Resumen</Link><Link href="/mapa">Mapa de estaciones</Link></nav><div className="side-note">Panel operativo<br /><small>Datos protegidos en servidor</small></div></aside>;
}
