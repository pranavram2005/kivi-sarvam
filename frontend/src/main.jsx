import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/kivi.css";
import "./styles/analytics.css";
import "./styles/heykivi.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
