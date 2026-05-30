import os
from django.shortcuts import render, redirect
from django.urls import reverse
from django.http import HttpResponse, JsonResponse
from artof_utils.robot_manager import robot_manager
from artof_utils.field_manager import FieldManager
from artof_utils.visualisation_manager import visualisation_manager
from app_core.utils.fields import Fields
from artof_utils.schemas.task import Task
from artof_utils.gis import traject
from artof_utils.gis import polygon
from artof_utils.gis import task
from artof_utils.gis import shape as shp
from artof_utils.schemas.settings import HitchName
import artof_utils.paths as paths
from app_core.utils.geojson import GeoJson, GeomType
from .forms.multifileinput import FileFieldForm
from glob import glob
from os import path, walk
import json
from copy import deepcopy
import io
import zipfile
from django.http import HttpResponse
from django.shortcuts import render


# Create your views here.
def create_context(data=dict()):
    data['robot_name'] = robot_manager.platform_settings.name.capitalize()
    data['navigation_state'] = {
        'states': ','.join(robot_manager.get_navigation_states()),
        'current_state': robot_manager.get_navigation_state()
    }

    return data

def notification_acknowledge(request):
    robot_manager.acknowledge_notification()
    return HttpResponse()

def navigation_state(request):
    new_state = request.POST.get("state")
    if new_state:
        robot_manager.set_navigation_state(new_state)
    return HttpResponse()

def reset_task(request):
    if request.method == 'POST':
        visualisation_manager.reset_task()
        robot_manager.update_field()
        return JsonResponse({'status': 'success', 'message': 'Taak gereset en gearchiveerd.'})

    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

# Field
def field(request):
    fields = Fields()
    return render(request, "app/field.html", context=create_context(fields.context))

def field_download(request):
    # Folder path to be zipped and downloaded
    field_name = request.GET.get('name')
    folder_path = path.join(paths.fields, field_name)

    if path.exists(folder_path):
        # Create an in-memory zip file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED, False) as zip_file:
            for root, dirs, files in walk(folder_path):
                for file in files:
                    file_path = path.join(root, file)
                    zip_file.write(file_path, path.relpath(file_path, folder_path))

        # Prepare the zip file for download
        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="%s.zip"' % field_name
        return response

def field_select(request):
    fields = Fields()
    fields.select_field(request.GET.get("name"))
    return redirect(reverse('core:field'))

def field_delete(request):
    fields = Fields()
    fields.delete_field(request.GET.get("name"))
    return redirect(reverse('core:field'))

def delete_new_field(request):
    fields = Fields()
    field_name = "New"
    fields.delete_field(field_name)
    return redirect(reverse('core:field'))

def field_duplicate(request):
    fields = Fields()
    fields.duplicate_field(request.GET.get("name"))
    return redirect(reverse('core:field'))

# Field Edit
def field_edit_geojson(request):
    if request.method == 'POST':
        uploaded_files = request.FILES.getlist('files')
        
        for uploaded_file in uploaded_files:
            if uploaded_file.name.lower().endswith(('.tif', '.tiff')):
                return JsonResponse({
                    'type': 'raster',
                    'name': uploaded_file.name,
                    'message': 'Raster bestand herkend.'
                })

        form = FileFieldForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                geo_file = form.load_geojson()
                if geo_file and geo_file.context:
                    return JsonResponse(geo_file.context)
                else:
                    return JsonResponse({'status': 'error', 'message': 'Ongeldig of leeg geojson bestand'}, status=400)
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': f'Fout bij inladen bestand: {str(e)}'}, status=400)

    return JsonResponse({'status': 'error', 'message': 'Geen bestand ontvangen'}, status=400)

def field_edit_context(field):
    field_context = field.context

    field_context['field_name'] = field.name
    field_context['field_json'] = field.json
    field_context['hitch_choices'] = [(hitch.name.value, hitch.name.value) for hitch in robot_manager.hitches.hitches]
    field_context['implement_choices'] = task.get_implement_choices()
    field_context['type_choices'] = task.get_type_choices()
    if 'tasks' in field_context:
        field_context['task_geometries'] = list(field_context['tasks'].values())
    else:
        field_context['task_geometries'] = []

    return field_context


def field_edit_task_add(request):
    field_name = request.GET.get('field_name')
    geo_file = GeoJson(os.path.join(paths.fields, field_name), "data")  
    field = FieldManager(field_name, geo_file.gdf)
    field.add_new_task()
    geo_file.gdf = field.gdf
    geo_file.save()
    #return render(request, 'app/field_edit.html', context=create_context(field_edit_context(field)))
    return redirect(f"{reverse('core:field_edit')}?name={field_name}")

def field_edit_task_remove(request):
    field_name = request.GET.get('field_name')
    geo_file = GeoJson(os.path.join(paths.fields, field_name), "data") 
    task_name = request.GET.get('task_name')
    base_raster_folder = os.path.join(paths.fields, field_name, 'rasters')
    tif_path = os.path.join(base_raster_folder, f"{task_name}.tif")
    png_path = os.path.join(base_raster_folder, f"{task_name}.png")
    if os.path.exists(tif_path):
        os.remove(tif_path)
    if os.path.exists(png_path):
        os.remove(png_path)
    field = FieldManager(field_name, geo_file.gdf)
    field.remove_task(task_name)
    geo_file.gdf = field.gdf
    geo_file.save()
    return render(request, 'app/field_edit.html', context=create_context(field_edit_context(field)))


def field_edit_name(request):
    original_field_name = request.POST.get('original')
    new_field_name = request.POST.get('new')
    Fields().rename_field(original_field_name, new_field_name)
    geo_file = GeoJson(os.path.join(paths.fields, new_field_name), "data")  
    field = FieldManager(new_field_name, geo_file.gdf)
    field.rename(new_field_name)
    geo_file.gdf = field.gdf
    geo_file.save()
    return redirect(reverse('core:field_edit') + '?name=' + new_field_name)

def field_edit_geofence(request):
    field_name = request.POST.get('name')
    geo_file = GeoJson(os.path.join(paths.fields, field_name), "data")
    field = FieldManager(field_name, geo_file.gdf)

    data = json.loads(request.POST.get('data'))
    resolution = 0.000001
    
    # 1. Bepaal de geometrie en coördinaten
    if data['empty'] or request.POST.get('input_mode') == 'drive':
        geom = GeomType.Polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)])
        coords = [[(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]]
        field.update_geofence(geom)
    else:
        geom_obj = extract_geojson(data)
        coords = geom_obj['coordinates']
        geoms = GeoJson.to_shapely(coords, type=GeomType.POLYGON)
        field.update_geofence(geoms[0])

    # 2. Synchroniseer en bewaar de vectoren in de GeoJSON
    geo_file.gdf = field.gdf
    geo_file.save()

    # 3. Genereer en bewaar het raster (geofence)
    geo_file.save_as_raster(
        name='geofence',
        data=coords,
        resolution=resolution,
        epsg=4326,
        type=GeomType.POLYGON
    )

    # 4. Sync terug naar de field manager voor de frontend rendering
    field.gdf = geo_file.gdf

    return render(request, 'app/field_edit.html', context=create_context(field_edit_context(field)))


def field_edit_traject(request):
    field_name = request.POST.get('name')
    geo_file = GeoJson(os.path.join(paths.fields, field_name), "data")
    field = FieldManager(field_name, geo_file.gdf)

    data = json.loads(request.POST.get('data'))
    resolution = 0.000001
    
    # 1. Bepaal de geometrie en coördinaten
    if data['empty'] or request.POST.get('input_mode') == 'drive':
        geom = GeomType.LineString([(0, 0), (10, 10)])
        coords = [[(0, 0), (10, 10)]]
        field.update_traject(geom)
    else:
        geom_obj = extract_geojson(data)
        coords = geom_obj['coordinates']
        geoms = GeoJson.to_shapely(coords, type=GeomType.LINESTRING)
        field.update_traject(geoms[0])

    # 2. Synchroniseer en bewaar de vectoren in de GeoJSON
    geo_file.gdf = field.gdf
    geo_file.save()

    # 3. Genereer en bewaar het raster (traject)
    # Let op: we gebruiken geo_file.save_as_raster en NIET GeoJson.save_as_raster
    geo_file.save_as_raster(
        name='traject',
        data=coords,
        resolution=resolution,
        epsg=4326,
        type=GeomType.LINESTRING
    )

    # 4. Sync terug naar de field manager voor de frontend rendering
    field.gdf = geo_file.gdf

    return render(request, 'app/field_edit.html', context=create_context(field_edit_context(field)))


def field_edit_task(request):
    field_name = request.POST.get('name')
    geo_file = GeoJson(os.path.join(paths.fields, field_name), "data")
    field = FieldManager(field_name, geo_file.gdf)

    task = json.loads(request.POST.get('data'))
    input_mode = request.POST.get('input_mode')
    resolution = 0.000001

    geometries = None
    coords = None
    geom_type = None

    # 1. Bepaal de geometrie en coördinaten
    if input_mode != 'original':
        task_geom_data = task.get('geometry', {})
        
        if not task_geom_data.get('empty', True) and 'geojson' in task_geom_data:
            geojson = task_geom_data['geojson']
            if geojson['type'] == 'FeatureCollection':
                coords = [feat['geometry']['coordinates'] for feat in geojson['features']]
            else:
                coords = geojson['geometry']['coordinates']

            if task['hitch_type'] in ['cardan', 'continuous', 'hitch']:
                print(task['hitch_type'], "krijgt POLYGON")
                geom_type = GeomType.POLYGON
            else:
                print(task['hitch_type'], "krijgt MULTIPOINT")
                geom_type = GeomType.MULTIPOINT 
            
            geometries = GeoJson.to_shapely(coords, type=geom_type)

    task_info = Task(name=task['name'], type=task['type'], hitch_type=task['hitch_type'], implement='' if not task['implement'] else task['implement'], hitch_name=task['hitch_name'])

    # 2. Update EERST de vectoren via de FieldManager, zodat save_as_raster straks de rij kan vinden!
    field.update_task(task['name'], geometries, task_info)
    
    # 3. Synchroniseer en bewaar de vectoren in de GeoJSON
    geo_file.gdf = field.gdf
    geo_file.save()

    # 4. Als er coördinaten zijn, genereer dan het raster en de properties
    if coords is not None:
        properties = {
            'type': task_info.type,
            'implement': task_info.implement,
            'hitch_type': task_info.hitch_type,
            'hitch_name': task_info.hitch_name,
        }

        geo_file.save_as_raster(
            name=task['name'],
            data=coords,
            resolution=resolution,
            properties=properties,
            epsg=4326, 
            type=geom_type
        )
        
        # 5. Sync terug naar de field manager voor de frontend rendering
        field.gdf = geo_file.gdf
    
    return render(request, 'app/field_edit.html', context=create_context(field_edit_context(field)))

def field_edit(request):
    field_name = request.GET.get('name')
    if field_name == "New":
        Fields().create_field(field_name)
    geo_file = GeoJson(os.path.join(paths.fields, field_name), "data")
    field = FieldManager(field_name, geo_file.gdf)
    return render(request, 'app/field_edit.html', context=create_context(field_edit_context(field)))


# Settings
def settings(request):
    # Update context
    robot_manager.navigation.update()
    robot_manager.hitches.update()

    # Get context
    navigation_context = robot_manager.navigation.context

    navigation_mode = {
        'mode': navigation_context['navigation_mode'],
        'modes': robot_manager.get_navigation_modes()
    }
    navigation_sliders = [
        {'name': 'Velocity non operational', 'base_id': 'non_operational_velocity', 'initial': navigation_context['non_operational_velocity'], 'min': robot_manager.platform_settings.auto_velocity.min, 'max': robot_manager.platform_settings.auto_velocity.max, 'step': 0.1, 'unit': 'm/s'},
        {'name': 'Velocity operational', 'base_id': 'operational_velocity', 'initial': navigation_context['operational_velocity'], 'min': robot_manager.platform_settings.auto_velocity.min, 'max': robot_manager.platform_settings.auto_velocity.max, 'step': 0.1, 'unit': 'm/s'},
        {'name': 'Weight factor (Pure Pursuit)', 'base_id': 'weight_factor', 'initial': navigation_context['weight_factor'], 'min': 0.0, 'max': 1.0, 'step': 0.05, 'unit': ''},
        {'name': 'Carrot distance (Pure Pursuit)', 'base_id': 'carrot_distance', 'initial': navigation_context['carrot_distance'], 'min': 1.0, 'max': 6.0, 'step': 0.5, 'unit': 'm'},
        {'name': 'Kp (Pure Pursuit)', 'base_id': 'kp_purepursuit', 'initial': navigation_context['kp_purepursuit'], 'min': 0.0, 'max': 2.0, 'step': 0.1, 'unit': ''},
        {'name': 'Ki (Pure Pursuit)', 'base_id': 'ki_purepursuit', 'initial': navigation_context['ki_purepursuit'], 'min': 0.0, 'max': 0.5, 'step': 0.01, 'unit': ''},
        {'name': 'Kd (Pure Pursuit)', 'base_id': 'kd_purepursuit', 'initial': navigation_context['kd_purepursuit'], 'min': 0.0, 'max': 0.5, 'step': 0.01, 'unit': ''},
        {'name': 'Kp (Steady State)', 'base_id': 'kp_steady_state', 'initial': navigation_context['kp_steady_state'], 'min': 0.0, 'max': 2.0, 'step': 0.1, 'unit': ''},
        {'name': 'Ki (Steady State)', 'base_id': 'ki_steady_state', 'initial': navigation_context['ki_steady_state'], 'min': 0.0, 'max': 0.5, 'step': 0.01, 'unit': ''},
        {'name': 'Kd (Steady State)', 'base_id': 'kd_steady_state', 'initial': navigation_context['kd_steady_state'], 'min': 0.0, 'max': 0.5, 'step': 0.01, 'unit': ''},
        {'name': 'Kp (Rough)', 'base_id': 'kp_rough', 'initial': navigation_context['kp_rough'], 'min': 0.0, 'max': 2.0, 'step': 0.1, 'unit': ''},
        {'name': 'Ki (Rough)', 'base_id': 'ki_rough', 'initial': navigation_context['ki_rough'], 'min': 0.0, 'max': 0.5, 'step': 0.01, 'unit': ''},
        {'name': 'Kd (Rough)', 'base_id': 'kd_rough', 'initial': navigation_context['kd_rough'], 'min': 0.0, 'max': 0.5, 'step': 0.01, 'unit': ''}
    ]

    hitches = [{"name": hitch.name.value, "min": hitch.min, "max": hitch.max, "setpoint": hitch.setpoint, 'float': hitch.float} for hitch in robot_manager.hitches.hitches]
    return render(request, "app/settings.html", context=create_context({"hitches": hitches, 
                                                                        "navigation_sliders": navigation_sliders,
                                                                        "navigation_mode": navigation_mode}))

def update_navigation_settings(request):
    if request.method == 'POST':
        robot_manager.navigation.change(navigation_mode=int(request.POST['navigation_mode']),
                                        non_operational_velocity=float(request.POST['non_operational_velocity']),
                                        operational_velocity=float(request.POST['operational_velocity']),
                                        weight_factor=float(request.POST['weight_factor']),
                                        kp_purepursuit=float(request.POST['kp_purepursuit']),
                                        ki_purepursuit=float(request.POST['ki_purepursuit']),
                                        kd_purepursuit=float(request.POST['kd_purepursuit']),
                                        kp_steady_state=float(request.POST['kp_steady_state']),
                                        ki_steady_state=float(request.POST['ki_steady_state']),
                                        kd_steady_state=float(request.POST['kd_steady_state']),
                                        kp_rough=float(request.POST['kp_rough']),
                                        ki_rough=float(request.POST['ki_rough']),
                                        kd_rough=float(request.POST['kd_rough']),
                                        carrot_distance=float(request.POST['carrot_distance']))

    return settings(request)

def update_hitch_settings(request):
    if request.method == 'POST':
        hitch_names = [name.value for name in HitchName]
        hitch_setpoints = {}
        for hitch_name in hitch_names:
            if hitch_name in request.POST:
                if 'float_' + hitch_name in request.POST:
                    hitch_setpoints[hitch_name] = 99
                else:
                    hitch_setpoints[hitch_name] = int(request.POST[hitch_name])
        robot_manager.hitches.change(hitch_setpoints=hitch_setpoints)

    return settings(request)

# Map
def map_context():
    field_name = Fields.get_current_field_name()  
    geo_file = GeoJson(os.path.join(paths.fields, field_name), "data")
    robot_manager.load_field(geo_file.gdf)
    map_context = robot_manager.field.context  
    map_context['field_name'] = field_name
    map_context['field_json'] = robot_manager.field.json
    map_context['simulation'] = {

        'active': robot_manager.get_simulation_mode(),
        'speed_factor': int(robot_manager.get_simulation_speed_factor())
    }
    map_context['task_geometries'] = list(map_context.get('tasks', {}).values())
    return map_context


def map(request):
    return render(request, "app/map.html", context=create_context(map_context()))

def map_simulation(request):
    robot_manager.set_simulation_mode(request.POST.get("simulation") == "on")
    return HttpResponse()

def map_simulation_speed_factor(request):
    robot_manager.set_simulation_speed_factor(float(request.POST.get("factor")))
    return HttpResponse()

def map_simulation_position(request):
    data = json.loads(request.body)
    robot_manager.set_position_latlon(data['lat'], data['lon'])
    return HttpResponse()

def map_edit_shape(request):
    shape = request.POST.get('shape')
    geometries_str = request.POST.get('geometries')

    if not geometries_str:
        return redirect(reverse('core:map'))

    data = json.loads(geometries_str)
    geom_obj = extract_geojson(data)
    if not geom_obj:
        return redirect(reverse('core:map'))

    coords = geom_obj['coordinates']

    field_name = robot_manager.field.name
    geo_file = GeoJson(os.path.join(paths.fields, field_name), "data")
    field = FieldManager(field_name, geo_file.gdf)

    if shape == 'traject':
        geoms = GeoJson.to_shapely(coords, type=GeomType.LINESTRING)
        field.update_traject(geoms[0])
    elif shape == 'geofence':
        geoms = GeoJson.to_shapely(coords, type=GeomType.POLYGON)
        field.update_geofence(geoms[0])
    else:  # task
        tm = field.get_task(shape)
        if tm and tm.info.type.value in ['cardan', 'continuous', 'hitch']:
            geom_type = GeomType.POLYGON
        else:
            geom_type = GeomType.MULTIPOINT
        try:
            geoms = GeoJson.to_shapely(coords, type=geom_type)
        except Exception:
            geoms = GeoJson.to_shapely(coords, type=GeomType.MULTIPOINT)
        field.update_task(task_name=shape, geometry=geoms[0] if geoms else None)

    geo_file.gdf = field.gdf
    geo_file.save()
    robot_manager.field = field
    robot_manager.update_field()

    return redirect(reverse('core:map'))


def map_edit_traject_operation(request):
    body_unicode = request.body.decode('utf-8')
    body = json.loads(body_unicode)
    operation = traject.Operation(body["operation"])
    commands = body["commands"] if "commands" in body else {}
    new_traject = traject.perform(operation, body["data"], **commands)

    wgs84_crs = 'EPSG:4326'  # WGS 84
    utm_crs = 'EPSG:326%d' % robot_manager.platform_settings.gps.utm_zone

    new_traject_latlng = shp.transform_crs(utm_crs, wgs84_crs, new_traject)

    return JsonResponse({'path': new_traject, 'latlng': new_traject_latlng})


def map_edit_polygon_operation(request):
    body_unicode = request.body.decode('utf-8')
    body = json.loads(body_unicode)
    operation = body["operation"]
    commands = body["commands"] if "commands" in body else {}
    new_polygons = traject.perform(operation, body["data"], **commands)

    wgs84_crs = 'EPSG:4326'  # WGS 84
    utm_crs = 'EPSG:326%d' % robot_manager.platform_settings.gps.utm_zone

    new_polygons_latlng = deepcopy(body["data"])        
    if operation == 'buffer':
        new_polygons = polygon.buffer(body["data"], commands['distance'])
        new_polygons_latlng = shp.transform_crs(utm_crs, wgs84_crs, new_polygons) 
    
    return JsonResponse({'rings': new_polygons, 'latlng': new_polygons_latlng})
    


def map_edit_traject_rows(request):
    body_unicode = request.body.decode('utf-8')
    body = json.loads(body_unicode)
    traject_rows = traject.get_rows(body["data"])

    wgs84_crs = 'EPSG:4326'  # WGS 84
    utm_crs = 'EPSG:326%d' % robot_manager.platform_settings.gps.utm_zone
    traject_rows_latlng = []
    for traject_row in traject_rows:
        traject_rows_latlng.append(shp.transform_crs(utm_crs, wgs84_crs, traject_row))

    return JsonResponse({'latlng': traject_rows_latlng})


def extract_geojson(data):
    """Haalt de pure geometrie uit GeoJSON, of het nu een FeatureCollection, Feature of Geometry is."""
    geojson = data.get('geojson')
    if not geojson: return None
    
    if geojson.get('type') == 'FeatureCollection':
        features = geojson.get('features', [])
        if features and len(features) > 0:
            return features[0].get('geometry')
        return None
        
    if geojson.get('type') == 'Feature':
        return geojson.get('geometry')
    
    return geojson