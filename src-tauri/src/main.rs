use std::{
    env,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
};

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

struct DaemonState {
    child: Mutex<Option<Child>>,
}

fn main() {
    tauri::Builder::default()
        .manage(DaemonState {
            child: Mutex::new(None),
        })
        .setup(|app| {
            start_daemon(app)?;
            create_tray(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run GLM Plan Watcher shell");
}

fn start_daemon(app: &tauri::App) -> tauri::Result<()> {
    let port = env::var("GLM_WATCHER_DAEMON_PORT").unwrap_or_else(|_| "8765".to_string());
    let daemon_bin = resolve_daemon_bin(app);
    let data_dir = app.path().app_data_dir()?;
    std::fs::create_dir_all(&data_dir)?;
    let db_path = data_dir.join("daemon.sqlite3");

    let mut command = Command::new(daemon_bin);
    command
        .arg("serve")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(&port)
        .arg("--db")
        .arg(db_path)
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = command.spawn()?;
    if let Some(state) = app.try_state::<DaemonState>() {
        *state.child.lock().expect("daemon state poisoned") = Some(child);
    }
    Ok(())
}

fn resolve_daemon_bin(app: &tauri::App) -> PathBuf {
    if let Ok(path) = env::var("GLM_WATCHER_DAEMON_BIN") {
        return PathBuf::from(path);
    }

    let sidecar_names = [
        "glm-plan-daemon",
        "glm-plan-daemon-aarch64-apple-darwin",
        "glm-plan-daemon-x86_64-apple-darwin",
    ];
    if let Ok(resource_dir) = app.path().resource_dir() {
        for name in sidecar_names {
            let candidate = resource_dir.join(name);
            if candidate.exists() {
                return candidate;
            }
        }
    }
    if let Ok(current_exe) = env::current_exe() {
        if let Some(exe_dir) = current_exe.parent() {
            for name in sidecar_names {
                let candidate = exe_dir.join(name);
                if candidate.exists() {
                    return candidate;
                }
            }
        }
    }

    PathBuf::from("glm-plan")
}

fn create_tray(app: &tauri::App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Show", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &quit])?;
    let mut builder = TrayIconBuilder::new().menu(&menu).on_menu_event(|app, event| {
        match event.id().as_ref() {
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "quit" => {
                stop_daemon(app);
                app.exit(0);
            }
            _ => {}
        }
    });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

fn stop_daemon(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<DaemonState>() {
        if let Some(mut child) = state.child.lock().expect("daemon state poisoned").take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}
