import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { PrimeReactProvider } from "primereact/api";

import "primereact/resources/themes/lara-light-cyan/theme.css";
import "primereact/resources/primereact.min.css";
import "primeicons/primeicons.css";
import "./index.css";
// Imported last so the theming overrides win over the base PrimeReact theme.
import "./themes.css";

import { App } from "./App";
import { AuthProvider } from "./lib/auth";
import { queryClient } from "./lib/queryClient";
import { ThemeProvider } from "./lib/theme";
import { ToastProvider } from "./lib/toast";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <PrimeReactProvider>
        <ToastProvider>
          <QueryClientProvider client={queryClient}>
            <AuthProvider>
              <BrowserRouter>
                <App />
              </BrowserRouter>
            </AuthProvider>
          </QueryClientProvider>
        </ToastProvider>
      </PrimeReactProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
