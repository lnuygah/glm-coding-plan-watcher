use std::{
    env,
    fs,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
};

use serde::{Deserialize, Serialize};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
    State,
};
use uuid::Uuid;

struct DaemonState {
    child: Mutex<Option<Child>>,
    handshake_path: Mutex<Option<PathBuf>>,
}

fn main() {
    tauri::Builder::default()
        .manage(DaemonState {
            child: Mutex::new(None),
            handshake_path: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![daemon_handshake])
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

#[derive(Clone, Debug, Deserialize, Serialize)]
struct DaemonHandshake {
    host: String,
    port: u16,
    token: String,
}

#[tauri::command]
fn daemon_handshake(state: State<'_, DaemonState>) -> Result<Option<DaemonHandshake>, String> {
    let path = {
        let guard = state
            .handshake_path
            .lock()
            .map_err(|_| "daemon state poisoned".to_string())?;
        guard.clone()
    };
    let Some(path) = path else {
        return Ok(None);
    };
    if !path.exists() {
        return Ok(None);
    }
    let text = fs::read_to_string(path).map_err(|error| error.to_string())?;
    match serde_json::from_str::<DaemonHandshake>(&text) {
        Ok(handshake) => Ok(Some(handshake)),
        Err(_) => Ok(None),
    }
}

fn start_daemon(app: &tauri::App) -> tauri::Result<()> {
    // 固定端口优先（便于理解/排障）；被占用时 daemon 自动回退到空闲端口，UI 以握手文件为准。
    let port = env::var("GLM_WATCHER_DAEMON_PORT").unwrap_or_else(|_| "8765".to_string());
    let daemon_bin = resolve_daemon_bin(app);
    let data_dir = app.path().app_data_dir()?;
    std::fs::create_dir_all(&data_dir)?;
    let db_path = data_dir.join("daemon.sqlite3");
    let handshake_path = env::var("GLM_WATCHER_DAEMON_HANDSHAKE")
        .map(PathBuf::from)
        .unwrap_or_else(|_| data_dir.join("daemon.handshake.json"));
    let token = env::var("GLM_WATCHER_DAEMON_TOKEN").unwrap_or_else(|_| Uuid::new_v4().to_string());
    let _ = fs::remove_file(&handshake_path);

    let mut command = Command::new(daemon_bin);
    command
        .arg("serve")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(&port)
        .arg("--db")
        .arg(db_path)
        .arg("--token")
        .arg(token)
        .arg("--handshake")
        .arg(&handshake_path)
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = command.spawn()?;
    if let Some(state) = app.try_state::<DaemonState>() {
        *state.child.lock().expect("daemon state poisoned") = Some(child);
        *state.handshake_path.lock().expect("daemon state poisoned") = Some(handshake_path);
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
        if let Some(path) = state.handshake_path.lock().expect("daemon state poisoned").take() {
            let _ = fs::remove_file(path);
        }
    }
}
