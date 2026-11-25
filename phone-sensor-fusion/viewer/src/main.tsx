import {StrictMode} from "react";
import {createRoot} from "react-dom/client";
import App from "./App";
import Tooltips from "./components/Tooltip";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><App /><Tooltips /></StrictMode>);
