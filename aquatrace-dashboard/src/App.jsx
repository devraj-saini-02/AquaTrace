import { NavLink, Route, Routes } from "react-router-dom";

import logo from "./assets/logo.png";
import AnalysisView from "./views/AnalysisView";
import IncidentDetailView from "./views/IncidentDetailView";
import IncidentsView from "./views/IncidentsView";
import ModelRegistryView from "./views/ModelRegistryView";
import NetworkMapView from "./views/NetworkMapView";
import SensorHealthView from "./views/SensorHealthView";
import SimulationControlView from "./views/SimulationControlView";

const NAV_ITEMS = [
  { to: "/", label: "Network Map", end: true },
  { to: "/analysis", label: "Analysis Dashboard" },
  { to: "/incidents", label: "Incidents" },
  { to: "/sensor-health", label: "Sensor Health" },
  { to: "/models", label: "Model Registry" },
  { to: "/simulation", label: "Simulation Control" },
];

export default function App() {
  return (
    <div className="app-root">
      <header className="app-topbar">
        <img src={logo} alt="" className="app-topbar-logo" />
        <div>
          <div className="app-topbar-title">AquaTrace</div>
          <div className="app-topbar-tagline">Contamination Detection System</div>
        </div>
      </header>
      <div className="app-shell">
        <nav className="app-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <main className="app-main">
          <Routes>
            <Route path="/" element={<NetworkMapView />} />
            <Route path="/analysis" element={<AnalysisView />} />
            <Route path="/incidents" element={<IncidentsView />} />
            <Route path="/incidents/:id" element={<IncidentDetailView />} />
            <Route path="/sensor-health" element={<SensorHealthView />} />
            <Route path="/models" element={<ModelRegistryView />} />
            <Route path="/simulation" element={<SimulationControlView />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
