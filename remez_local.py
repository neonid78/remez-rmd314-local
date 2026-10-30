import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import tinytuya


BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "settings.json"
DEVICES_FILE = BASE_DIR / "devices.json"

DP_NAMES = {
    "1": "Питание",
    "2": "Целевая влажность",
    "4": "Скорость вентилятора",
    "6": "Текущая влажность",
    "7": "Температура",
    "10": "Ионизатор",
    "16": "Блокировка от детей",
    "17": "Таймер",
    "19": "Ошибки",
    "20": "Сброс фильтра",
    "23": "Ресурс фильтра",
    "24": "Единицы температуры",
    "28": "Сброс времени работы",
}
FAN_LABELS = {"low": "Низкая", "high": "Высокая"}
FAN_VALUES = {label: value for value, label in FAN_LABELS.items()}
TIMER_LABELS = {"cancel": "Выключен", "1h": "1 час", "2h": "2 часа", "3h": "3 часа"}
TIMER_VALUES = {label: value for value, label in TIMER_LABELS.items()}
FAULT_LABELS = ("Бак полон", "Очистка", "E1", "CL", "CH", "LO", "COIL", "MOTOR")


class RemezApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Remez RMD-314 — локальное управление")
        self.minsize(820, 720)
        self.geometry("900x800")

        self.ip_var = tk.StringVar()
        self.device_id_var = tk.StringVar()
        self.local_key_var = tk.StringVar()
        self.version_var = tk.StringVar(value="3.3")
        self.auto_refresh_var = tk.BooleanVar(value=True)

        self.power_state_var = tk.StringVar(value="—")
        self.humidity_var = tk.StringVar(value="—")
        self.temperature_var = tk.StringVar(value="—")
        self.filter_var = tk.StringVar(value="—")
        self.fault_var = tk.StringVar(value="Состояние ошибок неизвестно")
        self.target_var = tk.StringVar(value="50")
        self.fan_var = tk.StringVar(value="Низкая")
        self.timer_var = tk.StringVar(value="Выключен")
        self.anion_var = tk.BooleanVar(value=False)
        self.child_lock_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Введите данные подключения и нажмите «Подключиться».")
        self.last_update_var = tk.StringVar(value="")

        self.results = queue.Queue()
        self.action_widgets = []
        self.current_power = None
        self.busy = False
        self.closing = False

        self._build_ui()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_results)
        self.after(1000, self._auto_refresh)

    def _build_ui(self):
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        connection = ttk.LabelFrame(main, text="Подключение", padding=10)
        connection.pack(fill="x")
        connection.columnconfigure(1, weight=1)

        ttk.Label(connection, text="IP-адрес:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(connection, textvariable=self.ip_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4)

        ttk.Label(connection, text="Device ID:").grid(row=1, column=0, sticky="w", pady=4)
        device_entry = ttk.Entry(connection, textvariable=self.device_id_var)
        device_entry.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4)
        device_entry.bind("<FocusOut>", lambda _event: self._load_wizard_credentials())

        ttk.Label(connection, text="Local Key:").grid(row=2, column=0, sticky="w", pady=4)
        self.key_entry = ttk.Entry(connection, textvariable=self.local_key_var, show="•")
        self.key_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)
        ttk.Checkbutton(connection, text="Показать", command=self._toggle_key).grid(row=2, column=3, sticky="w", padx=(8, 0))

        ttk.Label(connection, text="Версия Tuya:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(
            connection,
            textvariable=self.version_var,
            values=("3.3", "3.4", "3.5"),
            state="readonly",
            width=8,
        ).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=4)
        ttk.Checkbutton(connection, text="Обновлять каждые 5 секунд", variable=self.auto_refresh_var).grid(row=3, column=2, sticky="w")
        self.read_button = ttk.Button(connection, text="Подключиться", command=self.read_status)
        self.read_button.grid(row=3, column=3, sticky="e", pady=4)
        self.action_widgets.append(self.read_button)

        ttk.Label(
            connection,
            text="IP, Device ID и версия сохраняются в settings.json. Local Key загружается из devices.json и отдельно не сохраняется.",
            foreground="#666666",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True, pady=(10, 0))
        control_tab = ttk.Frame(notebook, padding=10)
        diagnostics_tab = ttk.Frame(notebook, padding=6)
        notebook.add(control_tab, text="Управление")
        notebook.add(diagnostics_tab, text="Диагностика")

        summary = ttk.Frame(control_tab)
        summary.pack(fill="x")
        for column in range(4):
            summary.columnconfigure(column, weight=1)
        self._add_card(summary, 0, "Питание", self.power_state_var)
        self._add_card(summary, 1, "Влажность", self.humidity_var)
        self._add_card(summary, 2, "Температура", self.temperature_var)
        self._add_card(summary, 3, "Ресурс фильтра", self.filter_var)

        self.power_button = ttk.Button(control_tab, text="Сначала подключитесь", command=self._toggle_power, state="disabled")
        self.power_button.pack(fill="x", pady=(12, 10))
        self.action_widgets.append(self.power_button)

        settings = ttk.LabelFrame(control_tab, text="Настройки осушителя", padding=10)
        settings.pack(fill="x")
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        ttk.Label(settings, text="Целевая влажность, %:").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Spinbox(settings, from_=30, to=80, increment=5, textvariable=self.target_var, width=8).grid(row=0, column=1, sticky="w", padx=(8, 8), pady=6)
        humidity_button = ttk.Button(settings, text="Применить", command=self._set_humidity)
        humidity_button.grid(row=0, column=2, sticky="w", pady=6)
        self.action_widgets.append(humidity_button)

        ttk.Label(settings, text="Вентилятор:").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(
            settings,
            textvariable=self.fan_var,
            values=tuple(FAN_VALUES),
            state="readonly",
            width=14,
        ).grid(row=1, column=1, sticky="w", padx=(8, 8), pady=6)
        fan_button = ttk.Button(settings, text="Применить", command=self._set_fan)
        fan_button.grid(row=1, column=2, sticky="w", pady=6)
        self.action_widgets.append(fan_button)

        ttk.Label(settings, text="Таймер:").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(
            settings,
            textvariable=self.timer_var,
            values=tuple(TIMER_VALUES),
            state="readonly",
            width=14,
        ).grid(row=2, column=1, sticky="w", padx=(8, 8), pady=6)
        timer_button = ttk.Button(settings, text="Применить", command=self._set_timer)
        timer_button.grid(row=2, column=2, sticky="w", pady=6)
        self.action_widgets.append(timer_button)

        anion_check = ttk.Checkbutton(
            settings,
            text="Ионизатор",
            variable=self.anion_var,
            command=lambda: self._write_value(10, self.anion_var.get(), "Настройка ионизатора применена"),
        )
        anion_check.grid(row=0, column=3, sticky="w", padx=(20, 0), pady=6)
        self.action_widgets.append(anion_check)

        child_lock_check = ttk.Checkbutton(
            settings,
            text="Блокировка от детей",
            variable=self.child_lock_var,
            command=lambda: self._write_value(16, self.child_lock_var.get(), "Настройка блокировки применена"),
        )
        child_lock_check.grid(row=1, column=3, sticky="w", padx=(20, 0), pady=6)
        self.action_widgets.append(child_lock_check)

        ttk.Label(control_tab, textvariable=self.fault_var, wraplength=780).pack(anchor="w", pady=(14, 0))

        self.tree = ttk.Treeview(diagnostics_tab, columns=("dp", "name", "value"), show="headings", height=15)
        self.tree.heading("dp", text="DP")
        self.tree.heading("name", text="Параметр")
        self.tree.heading("value", text="Значение")
        self.tree.column("dp", width=55, stretch=False, anchor="center")
        self.tree.column("name", width=240, stretch=False)
        self.tree.column("value", width=500)
        self.tree.pack(side="top", fill="both", expand=True)

        ttk.Label(diagnostics_tab, text="Последний ответ устройства:").pack(anchor="w", pady=(8, 0))
        self.raw_text = tk.Text(diagnostics_tab, height=9, wrap="word", state="disabled")
        self.raw_text.pack(fill="x", pady=(4, 0))

        status_line = ttk.Frame(main)
        status_line.pack(fill="x", pady=(10, 0))
        ttk.Label(status_line, textvariable=self.status_var, wraplength=680).pack(side="left", fill="x", expand=True)
        ttk.Label(status_line, textvariable=self.last_update_var).pack(side="right")

    @staticmethod
    def _add_card(parent, column, title, variable):
        card = ttk.LabelFrame(parent, text=title, padding=8)
        card.grid(row=0, column=column, sticky="nsew", padx=4)
        ttk.Label(card, textvariable=variable, anchor="center", font=("Segoe UI", 16)).pack(fill="x", expand=True)

    def _toggle_key(self):
        self.key_entry.configure(show="" if self.key_entry.cget("show") else "•")

    def _load_settings(self):
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            settings = {}
        self.ip_var.set(str(settings.get("ip", "")))
        self.device_id_var.set(str(settings.get("device_id", "")))
        self.version_var.set(str(settings.get("version", "3.3")))
        self.auto_refresh_var.set(bool(settings.get("auto_refresh", True)))
        self._load_wizard_credentials()

    def _load_wizard_credentials(self):
        try:
            devices = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(devices, list):
            return

        device_id = self.device_id_var.get().strip()
        selected = next((item for item in devices if item.get("id") == device_id), None)
        if selected is None and not device_id:
            selected = next(
                (item for item in devices if "remez" in str(item.get("name", "")).lower() or "rmd-314" in str(item.get("name", "")).lower()),
                None,
            )
        if selected is None:
            return

        self.device_id_var.set(str(selected.get("id", device_id)))
        key = selected.get("key") or selected.get("local_key")
        if key:
            self.local_key_var.set(str(key))
        if not self.ip_var.get().strip() and selected.get("ip"):
            self.ip_var.set(str(selected["ip"]))
        self.status_var.set("Device ID и Local Key загружены из devices.json. Укажите IP и подключитесь.")

    def _save_settings(self):
        settings = {
            "ip": self.ip_var.get().strip(),
            "device_id": self.device_id_var.get().strip(),
            "version": self.version_var.get(),
            "auto_refresh": self.auto_refresh_var.get(),
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _make_device(self):
        address = self.ip_var.get().strip()
        device_id = self.device_id_var.get().strip()
        local_key = self.local_key_var.get().strip()
        if not address or not device_id or not local_key:
            raise ValueError("Заполните IP-адрес, Device ID и Local Key.")

        device = tinytuya.Device(dev_id=device_id, address=address, local_key=local_key)
        device.set_version(float(self.version_var.get()))
        device.set_socketTimeout(5)
        device.set_socketPersistent(False)
        return device

    @staticmethod
    def _check_response(response):
        if not isinstance(response, dict):
            raise RuntimeError(f"Некорректный ответ устройства: {response!r}")
        if "Error" in response:
            error = response.get("Error", "Неизвестная ошибка")
            payload = response.get("Payload", "")
            raise RuntimeError(f"{error}: {payload}" if payload else str(error))
        return response

    def _read_status(self, device):
        response = self._check_response(device.status())
        if not isinstance(response.get("dps"), dict):
            raise RuntimeError(f"В ответе нет списка параметров dps: {response}")
        return response

    def _set_busy(self, busy):
        self.busy = busy
        for widget in self.action_widgets:
            widget.configure(state="disabled" if busy else "normal")
        if not busy and self.current_power is None:
            self.power_button.configure(state="disabled")

    def _start_task(self, operation, silent=False):
        if self.busy:
            return
        self._set_busy(True)
        self.status_var.set("Обмен данными с осушителем…")

        def worker():
            try:
                response, message = operation()
                self.results.put(("success", response, message, silent))
            except Exception as exc:
                self.results.put(("error", exc, "", silent))

        threading.Thread(target=worker, daemon=True).start()

    def read_status(self, silent=False):
        if self.busy:
            return
        try:
            device = self._make_device()
        except Exception as exc:
            if not silent:
                messagebox.showerror("Проверка подключения", str(exc), parent=self)
            return
        self._start_task(lambda: (self._read_status(device), "Состояние обновлено"), silent)

    def _write_value(self, dp_id, value, message):
        if self.busy:
            return
        try:
            device = self._make_device()
        except Exception as exc:
            messagebox.showerror("Проверка подключения", str(exc), parent=self)
            return

        def operation():
            self._check_response(device.set_value(dp_id, value))
            return self._read_status(device), message

        self._start_task(operation)

    def _toggle_power(self):
        if self.current_power is not None:
            self._write_value(1, not self.current_power, "Питание переключено")

    def _set_humidity(self):
        try:
            value = int(self.target_var.get())
            if value < 30 or value > 80 or value % 5:
                raise ValueError
        except ValueError:
            messagebox.showerror("Целевая влажность", "Укажите значение от 30 до 80 с шагом 5.", parent=self)
            return
        self._write_value(2, value, f"Целевая влажность установлена: {value}%")

    def _set_fan(self):
        self._write_value(4, FAN_VALUES[self.fan_var.get()], "Скорость вентилятора изменена")

    def _set_timer(self):
        self._write_value(17, TIMER_VALUES[self.timer_var.get()], "Настройка таймера применена")

    @staticmethod
    def _display(value, suffix=""):
        return "—" if value is None else f"{value}{suffix}"

    @staticmethod
    def _fault_text(value):
        if not isinstance(value, int) or value == 0:
            return "Ошибок нет"
        active = [label for bit, label in enumerate(FAULT_LABELS) if value & (1 << bit)]
        return "Ошибки: " + (", ".join(active) if active else f"код {value}")

    def _show_status(self, response):
        dps = response["dps"]
        if isinstance(dps.get("1"), bool):
            self.current_power = dps["1"]
            self.power_state_var.set("Включен" if self.current_power else "Выключен")
            self.power_button.configure(text="Выключить" if self.current_power else "Включить")

        self.humidity_var.set(self._display(dps.get("6"), " %"))
        self.temperature_var.set(self._display(dps.get("7"), " °C"))
        self.filter_var.set(self._display(dps.get("23"), " %"))
        self.fault_var.set(self._fault_text(dps.get("19", 0)))

        if dps.get("2") is not None:
            self.target_var.set(str(dps["2"]))
        if dps.get("4") in FAN_LABELS:
            self.fan_var.set(FAN_LABELS[dps["4"]])
        if dps.get("17") in TIMER_LABELS:
            self.timer_var.set(TIMER_LABELS[dps["17"]])
        if isinstance(dps.get("10"), bool):
            self.anion_var.set(dps["10"])
        if isinstance(dps.get("16"), bool):
            self.child_lock_var.set(dps["16"])

        self.tree.delete(*self.tree.get_children())
        for dp_id, value in sorted(dps.items(), key=lambda item: (not str(item[0]).isdigit(), int(item[0]) if str(item[0]).isdigit() else str(item[0]))):
            dp_text = str(dp_id)
            self.tree.insert("", "end", values=(dp_text, DP_NAMES.get(dp_text, "Другой параметр"), json.dumps(value, ensure_ascii=False)))
        self._set_raw(json.dumps(response, ensure_ascii=False, indent=2))

    def _set_raw(self, text):
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", text)
        self.raw_text.configure(state="disabled")

    def _poll_results(self):
        if self.closing:
            return
        try:
            while True:
                result_type, data, message, silent = self.results.get_nowait()
                if result_type == "success":
                    self._show_status(data)
                    self._set_busy(False)
                    self.status_var.set(message)
                    self.last_update_var.set("Обновлено " + datetime.now().strftime("%H:%M:%S"))
                    self._save_settings()
                else:
                    self._set_busy(False)
                    self.status_var.set(f"Ошибка связи: {data}")
                    self._set_raw(str(data))
                    if not silent:
                        messagebox.showerror("Ошибка связи", str(data), parent=self)
        except queue.Empty:
            pass
        self.after(100, self._poll_results)

    def _auto_refresh(self):
        if self.closing:
            return
        if self.auto_refresh_var.get() and not self.busy:
            if self.ip_var.get().strip() and self.device_id_var.get().strip() and self.local_key_var.get().strip():
                self.read_status(silent=True)
        self.after(5000, self._auto_refresh)

    def _on_close(self):
        self.closing = True
        self._save_settings()
        self.destroy()


if __name__ == "__main__":
    RemezApp().mainloop()
