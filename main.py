import flet as ft
import flet.canvas as cv
import math
import os
import traceback
import io
import platform
from datetime import datetime

# --- 依赖库检测 ---
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    from reportlab.lib import colors as pdf_colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as PdfImage
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

CONFIG_FILE = "furnace_config_mobile_v4.json"
EXCEL_FILE = "materials_db.xlsx"

# 颜色配置
APP_COLOR = ft.Colors.INDIGO
BG_COLOR = ft.Colors.GREY_50
CARD_BG = ft.Colors.WHITE

# 图表颜色 (UI用)
CHART_COLORS = [
    ft.Colors.AMBER_300, ft.Colors.LIGHT_BLUE_300, ft.Colors.LIGHT_GREEN_300, 
    ft.Colors.ORANGE_300, ft.Colors.PURPLE_300, ft.Colors.PINK_300
]
# 图表颜色 (PDF用 - RGB元组)
PIL_COLORS = [
    (255, 213, 79), (79, 195, 247), (174, 213, 129),
    (255, 183, 77), (186, 104, 200), (240, 98, 146)
]

# --- 1. 核心工具类 ---
class UnitConverter:
    UNITS = {
        "Length": {"mm": 0.001, "m": 1.0, "cm": 0.01, "in": 0.0254},
        "Temp": {"°C": "c", "K": "k", "°F": "f"},
        "Power": {"W": 1.0, "kW": 1000.0, "kcal/h": 1.16222, "MJ/h": 277.778},
        "Flux": {"W/m²": 1.0, "kW/m²": 1000.0, "kcal/(m²·h)": 1.16222},
        "Conductivity": {"W/(m·K)": 1.0, "kcal/(m·h·°C)": 1.16222},
        "Coeff": {"W/(m²·K)": 1.0, "kcal/(m²·h·°C)": 1.16222},
        "Velocity": {"m/s": 1.0, "km/h": 0.27778, "ft/min": 0.00508},
        "Area": {"m²": 1.0, "cm²": 0.0001, "ft²": 0.0929}
    }

    @staticmethod
    def to_si(val, unit, cat):
        if cat == "Temp":
            if unit == "°C": return val
            if unit == "K": return val - 273.15
            if unit == "°F": return (val - 32) * 5/9
        return val * UnitConverter.UNITS[cat][unit]

    @staticmethod
    def from_si(val, unit, cat):
        if cat == "Temp":
            if unit == "°C": return val
            if unit == "K": return val + 273.15
            if unit == "°F": return val * 9/5 + 32
        return val / UnitConverter.UNITS[cat][unit]

class MaterialManager:
    def __init__(self): self.data = self.load_materials()
    def load_materials(self):
        if HAS_PANDAS and os.path.exists(EXCEL_FILE):
            try:
                df = pd.read_excel(EXCEL_FILE)
                if {'name', 'a', 'b'}.issubset(df.columns): return df.to_dict('records')
            except: pass
        return [{"name": n, "a": a, "b": b} for n, a, b in zip(["耐火砖", "高铝砖", "保温砖", "硅酸铝纤维", "碳钢壳体", "空气层"], [0.8, 2.0, 0.15, 0.05, 45.0, 0.03], [0.0002, 0.0005, 0.0001, 0.00015, -0.02, 0.00005])]
    def save_db(self):
        if HAS_PANDAS:
            try: pd.DataFrame(self.data).to_excel(EXCEL_FILE, index=False)
            except: pass
    def get_names(self): return [d['name'] for d in self.data]
    def get_params(self, name):
        for d in self.data:
            if d['name'] == name: return float(d['a']), float(d['b'])
        return 1.0, 0.0
    def upsert(self, name, a, b):
        for d in self.data:
            if d['name'] == name: d['a'], d['b'] = float(a), float(b); return
        self.data.append({"name": name, "a": float(a), "b": float(b)})
    def delete(self, name): self.data = [d for d in self.data if d['name'] != name]
    def rename(self, old_name, new_name, a, b): self.delete(old_name); self.upsert(new_name, a, b)


# --- 2. Flet 主程序 ---

def main(page: ft.Page):
    # === 页面配置 ===
    page.title = "工业炉衬计算专家 v2.2"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = BG_COLOR
    page.padding = 0
    page.window_width = 450
    page.window_height = 850
    page.theme = ft.Theme(color_scheme_seed=APP_COLOR)

    # === 全局变量 ===
    mat_mgr = MaterialManager()
    inputs_refs = {} 
    layer_controls = [] 
    last_calc_result = None 
    
    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    # === UI 组件 ===
    
    def show_snack(msg, color=ft.Colors.RED):
        snack = ft.SnackBar(ft.Text(msg), bgcolor=color, behavior=ft.SnackBarBehavior.FLOATING)
        page.open(snack)

    def SectionCard(title, controls):
        return ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Text(title, weight=ft.FontWeight.BOLD, size=16, color=APP_COLOR),
                    padding=ft.padding.only(bottom=5)
                ),
                ft.Column(controls, spacing=15)
            ]),
            bgcolor=CARD_BG, padding=20, border_radius=10,
            shadow=ft.BoxShadow(spread_radius=1, blur_radius=3, color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK)),
            margin=ft.margin.only(bottom=10)
        )

    def create_input_with_unit(label, default_val, default_unit, key, unit_cat):
        current_unit = [default_unit]
        
        tf = ft.TextField(
            value=str(default_val), expand=True, text_size=15, dense=True,
            content_padding=12, border_color=ft.Colors.GREY_400,
            keyboard_type=ft.KeyboardType.NUMBER, label=label,
        )

        def on_unit_change(e):
            new_unit = e.control.value
            old_u = current_unit[0]
            try:
                val_display = float(tf.value)
                val_si = UnitConverter.to_si(val_display, old_u, unit_cat)
                val_new = UnitConverter.from_si(val_si, new_unit, unit_cat)
                tf.value = f"{val_new:.4g}"
                tf.update()
                current_unit[0] = new_unit
            except ValueError: pass

        dd = ft.Dropdown(
            options=[ft.dropdown.Option(u) for u in UnitConverter.UNITS[unit_cat].keys()],
            value=default_unit, width=85, dense=True, text_size=13, content_padding=10,
            border_color=ft.Colors.GREY_400, on_change=on_unit_change
        )
        
        inputs_refs[key] = {
            "tf": tf, "dd": dd, "cat": unit_cat, "label": label,
            "get_si": lambda: UnitConverter.to_si(float(tf.value), dd.value, unit_cat),
            "get_display": lambda: (tf.value, dd.value)
        }
        return ft.Row([tf, dd], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)

    # === Tab 1: 输入界面 ===
    
    basic_card = SectionCard("基础参数", [
        create_input_with_unit("内径 D_in", "1500", "mm", "D_in", "Length"),
        create_input_with_unit("炉高 H", "3", "m", "H", "Length"),
        create_input_with_unit("内壁温度", "1000", "°C", "t0", "Temp"),
        create_input_with_unit("环境温度", "25", "°C", "ta", "Temp"),
        ft.TextField(label="外壳发射率 ε", value="0.9", keyboard_type=ft.KeyboardType.NUMBER, dense=True, content_padding=12)
    ])
    # 手动添加非标准输入引用
    inputs_refs["eps"] = {"field": basic_card.content.controls[1].controls[4], "label": "外壳发射率", "get_display": lambda: (basic_card.content.controls[1].controls[4].value, "-")}

    env_card = SectionCard("环境条件", [
        ft.Dropdown(
            label="炉体位置", options=[ft.dropdown.Option("垂直炉壁"), ft.dropdown.Option("炉顶"), ft.dropdown.Option("炉底")],
            value="垂直炉壁", dense=True, content_padding=12
        ),
        create_input_with_unit("自然风速", "0.5", "m/s", "vnat", "Velocity"),
        create_input_with_unit("风机风量", "0", "m³/h", "qflow", "Area"),
        create_input_with_unit("风机出口直径", "500", "mm", "fdia", "Length"),
    ])
    inputs_refs["pos"] = {"field": env_card.content.controls[1].controls[0], "label": "炉体位置", "get_display": lambda: (env_card.content.controls[1].controls[0].value, "-")}
    inputs_refs["qflow"]["cat"] = None # 手动处理

    layer_list_container = ft.Column(spacing=10)

    def update_layer_preview(e=None):
        for c in layer_controls:
            nm = c['dd'].value
            if not nm: continue
            a, b = mat_mgr.get_params(nm)
            sign = '+' if b >= 0 else '-'
            c['preview'].value = f"λ = {a:.3f} {sign} {abs(b):.5f}t"
        page.update()

    def add_layer(e=None, mat_idx=0, thickness="115"):
        idx = len(layer_controls) + 1
        dd = ft.Dropdown(
            options=[ft.dropdown.Option(n) for n in mat_mgr.get_names()],
            value=mat_mgr.get_names()[mat_idx] if mat_idx < len(mat_mgr.get_names()) else mat_mgr.get_names()[0],
            expand=3, dense=True, text_size=14, content_padding=10, on_change=update_layer_preview
        )
        tf = ft.TextField(value=thickness, expand=2, dense=True, text_size=14, content_padding=10, keyboard_type=ft.KeyboardType.NUMBER)
        unit_dd = ft.Dropdown(options=[ft.dropdown.Option("mm"), ft.dropdown.Option("m"), ft.dropdown.Option("in")], value="mm", width=75, dense=True, text_size=12, content_padding=8)
        preview = ft.Text("...", size=11, color=ft.Colors.GREY_600)
        btn_del = ft.IconButton(ft.Icons.REMOVE_CIRCLE_OUTLINE, icon_color=ft.Colors.RED_400, icon_size=22, on_click=lambda e: remove_layer(row_wrapper))

        row_content = ft.Column([
            ft.Row([ft.Container(ft.Text(f"{idx}", weight="bold", color=ft.Colors.GREY_600), width=20), dd, tf, unit_dd, btn_del], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=5),
            ft.Container(preview, padding=ft.padding.only(left=30))
        ], spacing=2)

        row_wrapper = ft.Container(content=row_content, bgcolor=ft.Colors.GREY_50, padding=10, border_radius=8, border=ft.border.all(1, ft.Colors.GREY_300))
        layer_controls.append({"container": row_wrapper, "dd": dd, "tf": tf, "unit": unit_dd, "preview": preview})
        layer_list_container.controls.append(row_wrapper)
        update_layer_preview()
        page.update()

    def remove_layer(container):
        if len(layer_controls) <= 1: show_snack("至少保留一层"); return
        for i, item in enumerate(layer_controls):
            if item["container"] == container: layer_controls.pop(i); break
        layer_list_container.controls.remove(container)
        for i, item in enumerate(layer_controls): item["container"].content.controls[0].controls[0].content.value = f"{i+1}"
        page.update()

    layer_card = SectionCard("炉衬结构", [
        ft.Row([ft.Text("由内向外设置", size=12, color="grey"), ft.TextButton("管理材料库", icon=ft.Icons.SETTINGS, on_click=lambda e: open_material_manager(e))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        layer_list_container,
        ft.OutlinedButton("添加层 +", on_click=lambda e: add_layer(), style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
    ])

    tab_inputs_view = ft.ListView(
        controls=[ft.Container(height=10), basic_card, env_card, layer_card, 
                  ft.Container(content=ft.ElevatedButton("开始计算", on_click=lambda e: calculate(e), bgcolor=APP_COLOR, color=ft.Colors.WHITE, height=50, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10))), padding=ft.padding.symmetric(horizontal=20, vertical=10)), ft.Container(height=30)],
        expand=True, padding=10
    )

    # === Tab 2: 结果界面 ===
    canvas = cv.Canvas(width=200, height=200)
    legend_col = ft.Column(spacing=2)

    def ResultTile(label, value, unit, color=ft.Colors.BLACK):
        # 修复 Error 1: 移除 Row 中 Text 的 bottom 属性，改用 Container padding
        return ft.Container(
            content=ft.Column([
                ft.Text(label, size=12, color=ft.Colors.GREY_600),
                ft.Row([
                    ft.Text(value, size=20, weight=ft.FontWeight.BOLD, color=color),
                    ft.Container(content=ft.Text(unit, size=12, color=ft.Colors.GREY_600), padding=ft.padding.only(bottom=2))
                ], vertical_alignment=ft.CrossAxisAlignment.END, spacing=2)
            ]),
            bgcolor=ft.Colors.WHITE, padding=10, border_radius=8, border=ft.border.all(1, ft.Colors.GREY_200), expand=True
        )

    summary_row_1 = ft.Row(expand=True)
    summary_row_2 = ft.Row(expand=True)
    result_table = ft.DataTable(columns=[ft.DataColumn(ft.Text("参数")), ft.DataColumn(ft.Text("数值")), ft.DataColumn(ft.Text("单位"))], heading_row_height=30, data_row_min_height=30, border=ft.border.all(1, ft.Colors.GREY_200))
    temps_table = ft.DataTable(columns=[ft.DataColumn(ft.Text("位置")), ft.DataColumn(ft.Text("温度")), ft.DataColumn(ft.Text("单位"))], heading_row_height=30, data_row_min_height=30, border=ft.border.all(1, ft.Colors.GREY_200))

    tab_results_view = ft.ListView(
        controls=[
            ft.Container(height=10),
            ft.Container(content=ft.Column([ft.Text("结构示意图", weight="bold"), ft.Row([ft.Container(canvas, width=200, height=200, border=ft.border.all(1, ft.Colors.GREY_200), border_radius=100), ft.Container(content=ft.Column([ft.Text("图例 (由外向内)", size=10, color="grey"), legend_col], scroll=ft.ScrollMode.HIDDEN), height=200, expand=True)], alignment=ft.MainAxisAlignment.START)]), bgcolor=CARD_BG, padding=20, border_radius=10, margin=ft.margin.only(bottom=10)),
            ft.Container(content=ft.Column([ft.Row([ft.Text("核心指标", weight="bold"), ft.IconButton(ft.Icons.PICTURE_AS_PDF, on_click=lambda e: trigger_export(e), tooltip="导出PDF")], alignment="spaceBetween"), summary_row_1, ft.Container(height=5), summary_row_2]), bgcolor=CARD_BG, padding=20, border_radius=10, margin=ft.margin.only(bottom=10)),
            SectionCard("详细参数", [result_table]),
            SectionCard("温度分布", [temps_table]),
            ft.Container(height=30)
        ], expand=True, padding=10
    )

    # === 计算逻辑 ===
    def calculate(e):
        try:
            D = inputs_refs["D_in"]["get_si"]()
            H = inputs_refs["H"]["get_si"]()
            t0 = inputs_refs["t0"]["get_si"]()
            ta = inputs_refs["ta"]["get_si"]()
            eps = float(inputs_refs["eps"]["field"].value)
            vnat = inputs_refs["vnat"]["get_si"]()
            q_val = float(inputs_refs["qflow"]["tf"].value)
            q_unit = inputs_refs["qflow"]["dd"].value
            q_si = q_val if q_unit=="m³/s" else q_val/3600.0
            fdia = inputs_refs["fdia"]["get_si"]()
            
            vfan = 0
            if q_si > 0 and fdia > 0: vfan = q_si / (math.pi * (fdia/2)**2)
            vtot = vnat + vfan
            xi = math.sqrt((vtot+0.348)/0.348)
            
            pos_val = inputs_refs["pos"]["field"].value
            Cpos = 2.8 if "顶" in pos_val else (1.5 if "底" in pos_val else 2.2)

            layers = []
            rc = D/2
            for item in layer_controls:
                nm = item["dd"].value
                tv_disp = float(item["tf"].value)
                tu_disp = item["unit"].value
                tsi = UnitConverter.to_si(tv_disp, tu_disp, "Length")
                asi, bsi = mat_mgr.get_params(nm)
                rn = rc + tsi
                layers.append({"a":asi, "b":bsi, "ri":rc, "ro":rn, "nm":nm, "thick_si":tsi, "thick_disp": tv_disp, "unit_disp": tu_disp})
                rc = rn
            
            Aout = 2*math.pi*rc*H 
            low, high = ta, t0
            found = False
            
            for _ in range(60):
                ts = (low+high)/2
                dt = ts - ta
                if dt < 1e-3: dt = 1e-3
                hnat = Cpos * (dt**0.25)
                hconv = hnat * xi
                hrad = eps * 5.67e-8 * ((ts+273.15)**4 - (ta+273.15)**4) / dt
                htot = hconv + hrad
                Q = htot * Aout * dt
                
                curr = ts
                temps = [ts]
                for l in reversed(layers):
                    ql = Q / H
                    term = (ql * math.log(l["ro"]/l["ri"])) / (2*math.pi)
                    ti = curr + 10
                    for _ in range(5):
                        tavg = (curr+ti)/2
                        lam = l["a"] + l["b"]*tavg
                        if lam < 0.01: lam = 0.01
                        tn = curr + term/lam
                        if abs(tn-ti)<0.1: ti=tn; break
                        ti=tn
                    curr = ti
                    temps.append(curr)
                
                if curr < t0: low = ts
                else: high = ts
                
                if abs(high-low) < 0.05:
                    found = True
                    temps_rev = list(reversed(temps))
                    nonlocal last_calc_result
                    result_data = {
                        "Q": Q, "q": Q/Aout, "ts": ts, "h_tot": htot, "h_conv": hconv, "h_rad": hrad,
                        "xi": xi, "v_tot": vtot, "temps": temps_rev, "Aout": Aout,
                        "inputs": {k: v["get_display"]() for k, v in inputs_refs.items() if "get_display" in v}
                    }
                    last_calc_result = {"res": result_data, "layers": layers, "D": D}
                    
                    summary_row_1.controls = [ResultTile("总散热 Q", f"{Q:.0f}", "W", ft.Colors.ORANGE_700), ResultTile("热流密度 q", f"{Q/Aout:.0f}", "W/m²", ft.Colors.RED_700)]
                    summary_row_2.controls = [ResultTile("外壁温度", f"{ts:.1f}", "°C", ft.Colors.BLUE_700), ResultTile("表面积", f"{Aout:.1f}", "m²")]
                    summary_row_1.update(); summary_row_2.update()
                    
                    result_table.rows.clear()
                    def add_r(p, v, u): result_table.rows.append(ft.DataRow([ft.DataCell(ft.Text(p)), ft.DataCell(ft.Text(v)), ft.DataCell(ft.Text(u))]))
                    add_r("综合换热系数 h", f"{htot:.2f}", "W/(m²K)")
                    add_r("  - 对流部分", f"{hconv:.2f}", "W/(m²K)")
                    add_r("  - 辐射部分", f"{hrad:.2f}", "W/(m²K)")
                    add_r("计算风速", f"{vtot:.2f}", "m/s")
                    add_r("修正系数 ξ", f"{xi:.2f}", "-")
                    
                    temps_table.rows.clear()
                    temps_table.rows.append(ft.DataRow([ft.DataCell(ft.Text("内壁(验算)")), ft.DataCell(ft.Text(f"{temps_rev[0]:.1f}")), ft.DataCell(ft.Text("°C"))]))
                    for i, t_val in enumerate(temps_rev[1:]):
                        lbl = "外壁表面" if i == len(temps_rev)-2 else f"层 {i+1} 界面"
                        temps_table.rows.append(ft.DataRow([ft.DataCell(ft.Text(lbl)), ft.DataCell(ft.Text(f"{t_val:.1f}")), ft.DataCell(ft.Text("°C"))]))
                    
                    draw_furnace_ui(D, layers)
                    tabs.selected_index = 1
                    page.update()
                    break
            
            if not found: show_snack("计算未收敛，请检查参数")

        except Exception as e:
            traceback.print_exc()
            show_snack(f"计算错误: {e}")

    def draw_furnace_ui(D, layers):
        canvas.shapes.clear()
        legend_col.controls.clear()
        Rin = D/2
        ts_list = [l["ro"] - l["ri"] for l in layers]
        Rout = Rin + sum(ts_list)
        cx, cy = 100, 100
        max_r = 95
        scale = max_r / Rout if Rout > 0 else 1
        current_r = Rout
        for i in range(len(ts_list)-1, -1, -1):
            r_px = current_r * scale
            color = CHART_COLORS[i % len(CHART_COLORS)]
            mat_name = layers[i]["nm"]
            th_disp = f"{layers[i]['thick_disp']}{layers[i]['unit_disp']}"
            
            canvas.shapes.append(cv.Circle(cx, cy, r_px, paint=ft.Paint(style=ft.PaintingStyle.FILL, color=color)))
            canvas.shapes.append(cv.Circle(cx, cy, r_px, paint=ft.Paint(style=ft.PaintingStyle.STROKE, color=ft.Colors.GREY_600)))
            legend_col.controls.append(ft.Row([ft.Container(width=12, height=12, bgcolor=color, border_radius=3), ft.Column([ft.Text(f"{mat_name}", size=11, weight="bold"), ft.Text(th_disp, size=10, color="grey")], spacing=0)], spacing=8))
            current_r -= ts_list[i]
        
        r_in_px = Rin * scale
        canvas.shapes.append(cv.Circle(cx, cy, r_in_px, paint=ft.Paint(style=ft.PaintingStyle.FILL, color=ft.Colors.WHITE)))
        canvas.shapes.append(cv.Circle(cx, cy, r_in_px, paint=ft.Paint(style=ft.PaintingStyle.STROKE, color=ft.Colors.RED, stroke_dash_pattern=[4,2])))
        canvas.shapes.append(cv.Text(cx-15, cy-5, "HOT", style=ft.TextStyle(color=ft.Colors.RED, weight=ft.FontWeight.BOLD)))
        canvas.update()
        legend_col.update()

    # === 材料库 ===
    def open_material_manager(e):
        def edit_item(old_name):
            a, b = mat_mgr.get_params(old_name)
            dlg_edit = ft.AlertDialog(title=ft.Text("编辑材料"))
            tf_n = ft.TextField(label="名称", value=old_name, dense=True)
            tf_a = ft.TextField(label="系数 a", value=str(a), keyboard_type=ft.KeyboardType.NUMBER, dense=True)
            tf_b = ft.TextField(label="系数 b", value=str(b), keyboard_type=ft.KeyboardType.NUMBER, dense=True)
            def save_edit(e):
                try:
                    na, nb = float(tf_a.value), float(tf_b.value)
                    if tf_n.value != old_name: mat_mgr.rename(old_name, tf_n.value, na, nb)
                    else: mat_mgr.upsert(tf_n.value, na, nb)
                    mat_mgr.save_db()
                    refresh_table()
                    for l in layer_controls: l['dd'].options = [ft.dropdown.Option(n) for n in mat_mgr.get_names()]; l['dd'].update()
                    page.close(dlg_edit)
                except: pass
            dlg_edit.content = ft.Column([tf_n, tf_a, tf_b], height=200, width=280)
            dlg_edit.actions = [ft.TextButton("保存", on_click=save_edit), ft.TextButton("取消", on_click=lambda e: page.close(dlg_edit))]
            page.open(dlg_edit)
            
        def add_item_dialog(e):
            dlg_add = ft.AlertDialog(title=ft.Text("新增材料"))
            tf_n = ft.TextField(label="名称", dense=True)
            tf_a = ft.TextField(label="系数 a", value="1.0", dense=True)
            tf_b = ft.TextField(label="系数 b", value="0.0", dense=True)
            def confirm_add(e):
                if not tf_n.value: return
                try: mat_mgr.upsert(tf_n.value, float(tf_a.value), float(tf_b.value)); mat_mgr.save_db(); refresh_table(); page.close(dlg_add)
                except: pass
            dlg_add.content = ft.Column([tf_n, tf_a, tf_b], height=200, width=280)
            dlg_add.actions = [ft.TextButton("确定", on_click=confirm_add), ft.TextButton("取消", on_click=lambda e: page.close(dlg_add))]
            page.open(dlg_add)

        mat_table = ft.DataTable(columns=[ft.DataColumn(ft.Text("名称")), ft.DataColumn(ft.Text("a")), ft.DataColumn(ft.Text("b")), ft.DataColumn(ft.Text("操作"))])
        def refresh_table():
            mat_table.rows.clear()
            for d in mat_mgr.data:
                mat_table.rows.append(ft.DataRow([ft.DataCell(ft.Text(d['name'])), ft.DataCell(ft.Text(f"{d['a']:.2f}")), ft.DataCell(ft.Text(f"{d['b']:.5f}")), ft.DataCell(ft.Row([ft.IconButton(ft.Icons.EDIT, icon_size=18, on_click=lambda e, n=d['name']: edit_item(n)), ft.IconButton(ft.Icons.DELETE, icon_size=18, icon_color="red", on_click=lambda e, n=d['name']: (mat_mgr.delete(n), mat_mgr.save_db(), refresh_table()))]))]))
            page.update()
        refresh_table()
        page.open(ft.AlertDialog(title=ft.Text("材料库"), content=ft.Container(content=ft.ListView([mat_table], height=300), width=350, height=300), actions=[ft.TextButton("新增", on_click=add_item_dialog), ft.TextButton("关闭", on_click=lambda e: page.close(page.dialogs[-1]))]))

    # === PDF 导出 (全面升级) ===
    def get_chinese_font():
        """查找可用中文字体"""
        system = platform.system()
        font_path = None
        if system == "Windows": font_path = os.path.join(os.environ["WINDIR"], "Fonts", "msyh.ttc")
        elif system == "Android" or system == "Linux": 
            paths = ["/system/fonts/DroidSansFallback.ttf", "/usr/share/fonts/truetype/droid/DroidSansFallback.ttf"]
            for p in paths:
                if os.path.exists(p): font_path = p; break
        return font_path

    def export_pdf_action(e: ft.FilePickerResultEvent):
        if not e.path: return
        if not (HAS_REPORTLAB and HAS_PIL): show_snack("缺失依赖库"); return
        try:
            doc = SimpleDocTemplate(e.path, pagesize=A4)
            elements = []
            font_path = get_chinese_font()
            font_name = 'Helvetica'
            if font_path and os.path.exists(font_path):
                pdfmetrics.registerFont(TTFont('CNFont', font_path))
                font_name = 'CNFont'
            
            styles = getSampleStyleSheet()
            style_h = ParagraphStyle('H', parent=styles['Heading1'], fontName=font_name, fontSize=16, spaceAfter=12)
            style_n = ParagraphStyle('N', parent=styles['Normal'], fontName=font_name, fontSize=10, leading=14)
            
            elements.append(Paragraph("炉衬热工计算报告", style_h))
            elements.append(Paragraph(f"生成日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}", style_n))
            elements.append(Spacer(1, 15))
            
            # 1. 绘制高清图
            if last_calc_result:
                layers = last_calc_result["layers"]
                D = last_calc_result["D"]
                ts_list = [l["ro"] - l["ri"] for l in layers]
                Rout = D/2 + sum(ts_list)
                
                img_size = 800
                img = Image.new("RGB", (img_size, img_size), "white")
                draw = ImageDraw.Draw(img)
                try: font_img = ImageFont.truetype(font_path, 24)
                except: font_img = ImageFont.load_default()
                
                cx, cy = img_size/2, img_size/2
                scale = (img_size/2 - 40) / Rout
                cur_r = Rout
                for i in range(len(ts_list)-1, -1, -1):
                    r_px = cur_r * scale
                    draw.ellipse([cx-r_px, cy-r_px, cx+r_px, cy+r_px], fill=PIL_COLORS[i%6], outline="black")
                    # 标注材料
                    if ts_list[i]*scale > 20: draw.text((cx, cy-r_px+10), f"{i+1}", fill="black", font=font_img)
                    cur_r -= ts_list[i]
                
                r_in = D/2 * scale
                draw.ellipse([cx-r_in, cy-r_in, cx+r_in, cy+r_in], fill="white", outline="red", width=2)
                draw.text((cx-20, cy-10), "HOT", fill="red", font=font_img)
                
                buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
                elements.append(PdfImage(buf, width=300, height=300))
                elements.append(Spacer(1, 15))

            # 表格样式
            tbl_style = TableStyle([('FONT', (0,0), (-1,-1), font_name), ('GRID', (0,0), (-1,-1), 0.5, pdf_colors.grey), ('BACKGROUND', (0,0), (-1,0), pdf_colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER')])
            
            # 2. 输入参数
            elements.append(Paragraph("一、输入参数", style_h))
            in_data = [["参数", "数值", "单位"]]
            if last_calc_result and "inputs" in last_calc_result["res"]:
                for k, v in last_calc_result["res"]["inputs"].items():
                    # 查找对应中文名
                    lbl = inputs_refs[k]["label"] if k in inputs_refs and "label" in inputs_refs[k] else k
                    in_data.append([lbl, str(v[0]), str(v[1])])
            elements.append(Table(in_data, colWidths=[200, 100, 100], style=tbl_style))
            elements.append(Spacer(1, 15))
            
            # 3. 炉衬结构
            elements.append(Paragraph("二、炉衬结构", style_h))
            lay_data = [["层号", "材料", "厚度", "导热系数(W/mK)"]]
            for i, l in enumerate(layers):
                sign = '+' if l['b'] >= 0 else '-'
                lay_data.append([str(i+1), l['nm'], f"{l['thick_disp']}{l['unit_disp']}", f"{l['a']:.3f}{sign}{abs(l['b']):.5f}t"])
            elements.append(Table(lay_data, colWidths=[40, 150, 80, 180], style=tbl_style))
            elements.append(Spacer(1, 15))

            # 4. 计算结果
            elements.append(Paragraph("三、计算结果", style_h))
            res = last_calc_result["res"]
            res_data = [["项目", "数值", "单位"], ["总散热损失 Q", f"{res['Q']:.2f}", "W"], ["外壁热流密度 q", f"{res['q']:.2f}", "W/m²"], ["外壁温度", f"{res['ts']:.2f}", "°C"], ["综合换热系数 h", f"{res['h_tot']:.2f}", "W/(m²K)"], ["计算总风速", f"{res['v_tot']:.2f}", "m/s"]]
            elements.append(Table(res_data, colWidths=[200, 100, 100], style=tbl_style))
            elements.append(Spacer(1, 15))
            
            # 5. 温度分布
            elements.append(Paragraph("四、温度分布", style_h))
            t_data = [["位置", "温度(°C)"]]
            temps = res["temps"]
            t_data.append(["内壁", f"{temps[0]:.1f}"])
            for i, t in enumerate(temps[1:]):
                lbl = "外壁表面" if i == len(temps)-2 else f"层 {i+1} 界面"
                t_data.append([lbl, f"{t:.1f}"])
            elements.append(Table(t_data, colWidths=[200, 100], style=tbl_style))
            
            doc.build(elements)
            show_snack(f"PDF 已保存: {e.path}", ft.Colors.GREEN)
        except Exception as ex: show_snack(f"导出失败: {ex}")

    def trigger_export(e):
        if not last_calc_result: show_snack("请先计算"); return
        file_picker.save_file(dialog_title="导出PDF报告", file_name="炉衬计算报告.pdf", allowed_extensions=["pdf"])
    file_picker.on_result = export_pdf_action

    tabs = ft.Tabs(selected_index=0, animation_duration=300, tabs=[ft.Tab(text="输入 & 结构", icon=ft.Icons.INPUT, content=tab_inputs_view), ft.Tab(text="结果 & 报告", icon=ft.Icons.ANALYTICS, content=tab_results_view)], expand=True)
    page.add(tabs)
    add_layer(None, 0, "115")
    add_layer(None, 1, "230")

ft.app(target=main)