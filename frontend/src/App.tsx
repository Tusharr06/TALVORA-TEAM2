import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Desk } from "./pages/Desk";
import { Evidence } from "./pages/Evidence";
import { Gate } from "./pages/Gate";
import { Intake } from "./pages/Intake";
import { People } from "./pages/People";
import { Pipeline } from "./pages/Pipeline";
import { Vacancies } from "./pages/Vacancies";
import { WorkspaceProvider } from "./state";

export function App() {
  return (
    <WorkspaceProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Gate />} />
          <Route element={<Shell />}>
            <Route path="/desk" element={<Desk />} />
            <Route path="/vacancies" element={<Vacancies />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/people" element={<People />} />
            <Route path="/intake" element={<Intake />} />
            <Route path="/evidence" element={<Evidence />} />
          </Route>
          <Route path="*" element={<Gate />} />
        </Routes>
      </BrowserRouter>
    </WorkspaceProvider>
  );
}
