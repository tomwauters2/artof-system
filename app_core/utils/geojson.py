from typing import Union, Any, Optional
import numpy as np
import pandas as pd
import geopandas as gpd
import os
import json
from os import path, makedirs
import rasterio
from enum import Enum
from shapely.geometry import shape, Point, Polygon, LineString, MultiPoint
from shapely.ops import unary_union
from PIL import Image
from artof_utils.gis.utils import array
from artof_utils.gis.raster import Raster as rstr

class GeomType(str, Enum):
    POINT = 'Point'
    MULTIPOINT = 'MultiPoint'
    LINESTRING = 'LineString'
    POLYGON = 'Polygon'

class GeoJson:
    def __init__(self, folder_path: str, filename: str = "data"):
        self.folder_path = folder_path
        self.filename = filename
        self.file_path = path.join(folder_path, f"{filename}.geojson")
        self.raster_path = os.path.join(self.folder_path, "rasters")
        self.gdf = None
        self.load()

    def load(self):
        """loads the file if it exists or else a empty GeoDataFrame."""
        default_columns = ['name', 'type', 'geometry', 'raster_source']

        if path.exists(self.file_path):
            try:
                self.gdf = gpd.read_file(self.file_path)
                if 'name' not in self.gdf.columns:
                    self.gdf['name'] = None
                # Fix CRS mismatch: file may declare EPSG:4326 but contain UTM coordinates
                if not self.gdf.empty and self.gdf.crs:
                    bounds = self.gdf.total_bounds  # (minx, miny, maxx, maxy)
                    if abs(bounds[0]) > 180 or abs(bounds[1]) > 90:
                        # Coordinates are out of lat/lng range → UTM
                        # Detect UTM zone from easting (x)
                        utm_zone = int((bounds[0] + 180) / 6) + 1
                        utm_crs = f"EPSG:326{utm_zone:02d}"
                        self.gdf = self.gdf.set_crs(utm_crs, allow_override=True)
                        self.gdf = self.gdf.to_crs("EPSG:4326")
            except Exception as e:
                print(f"Fout bij laden van {self.file_path}: {e}")
                self.gdf = gpd.GeoDataFrame(columns=default_columns, crs="EPSG:4326")
        else:
            self.gdf = gpd.GeoDataFrame(columns=default_columns, crs="EPSG:4326")

    @staticmethod
    def to_shapely(data, type: GeomType) -> list:
        geometries = data
        if geometries is None:
            return [None]
        
        if isinstance(geometries, gpd.GeoDataFrame):
            return geometries.geometry.tolist()
        
        if hasattr(geometries, 'geom_type'):
            return [geometries]

        geoms = []
        if isinstance(geometries, dict):
            geoms = [shape(geometries)]
        elif isinstance(geometries, (list, np.ndarray)):
            raw_list = geometries.tolist() if isinstance(geometries, np.ndarray) else geometries
            if not raw_list:
                return [None]
            
            depth = array.get_depth(raw_list)

            if type == GeomType.POLYGON:
                if depth == 2:
                    rings = [raw_list]
                elif depth == 3:
                    rings = raw_list
                elif depth == 4:
                    rings = raw_list[0]
                else:
                    rings = []

                for ring in rings:
                    ring_arr = np.array(ring)
                    if ring_arr.ndim == 2 and ring_arr.shape[1] == 2 and len(ring_arr) >= 3:
                        if not np.allclose(ring_arr[0], ring_arr[-1]):
                            ring_arr = np.vstack((ring_arr, ring_arr[0]))
                        geoms.append(Polygon(ring_arr))
                
                if geoms: return geoms

            if type == GeomType.MULTIPOINT:
                    pts = np.array(raw_list)
                    pts_cleaned = pts.squeeze()
                    
                    if pts_cleaned.ndim == 1:
                        pts_cleaned = pts_cleaned.reshape(1, 2)
                    
                    if pts_cleaned.ndim > 2:
                        pts_cleaned = pts_cleaned.reshape(-1, 2)

                    geoms = [MultiPoint(pts_cleaned.tolist())]
            elif type == GeomType.LINESTRING or (type is None and depth == 2):
                geoms = [LineString(raw_list)]
            elif depth == 1:
                geoms = [Point(raw_list)]
            elif depth >= 3:
                rings = raw_list[0] if depth == 4 else raw_list
                for ring in rings:
                    ring_arr = np.array(ring)
                    if ring_arr.ndim == 2 and len(ring_arr) >= 3:
                        if not np.allclose(ring_arr[0], ring_arr[-1]):
                            ring_arr = np.vstack((ring_arr, ring_arr[0]))
                        geoms.append(Polygon(ring_arr))
        
        return geoms if geoms else [None]


    def update(self, geometries: Union[list, np.ndarray, gpd.GeoDataFrame, None, dict], 
               name: str, 
               type: GeomType, 
               properties: Optional[dict] = None, 
               epsg: int = 0):
      
        shapely_geom = self.to_shapely(geometries, type)

        input_crs = f"EPSG:{epsg}" if epsg else "EPSG:4326"
        new_gdf = gpd.GeoDataFrame(
            {'name': [name] * len(shapely_geom), 'type': [str(type)] * len(shapely_geom)}, 
            geometry=shapely_geom, 
            crs=input_crs
        )

        if properties:
            for key, value in properties.items():
                new_gdf[key] = value

        if new_gdf.crs and new_gdf.crs.to_epsg() != 4326:
            new_gdf = new_gdf.to_crs(epsg=4326)

        if self.gdf is not None and not self.gdf.empty:
            # Zorg dat kolommen matchen
            for col in new_gdf.columns:
                if col not in self.gdf.columns: self.gdf[col] = None
            for col in self.gdf.columns:
                if col not in new_gdf.columns: new_gdf[col] = None

            self.gdf = self.gdf[self.gdf['name'] != name]
            self.gdf = pd.concat([self.gdf, new_gdf], ignore_index=True)
        else:
            self.gdf = new_gdf

        self.save()

    def save(self):
        if self.gdf is not None:
            if not path.exists(self.folder_path):
                makedirs(self.folder_path, exist_ok=True)
            
            raster_path = path.join(self.folder_path, "rasters")
            makedirs(raster_path, exist_ok=True)
                
            self.gdf.to_file(self.file_path, driver='GeoJSON')

    def delete(self, name: str):
        """
        Verwijdert een onderdeel uit de GDF en schoont bijbehorende bestanden op.
        """
        if self.gdf is None or self.gdf.empty:
            return

        item_to_delete = self.gdf[self.gdf['name'] == name]
        if item_to_delete.empty:
            print(f"Item '{name}' niet gevonden in GeoJSON.")
            return

        potential_raster = path.join(self.folder_path, "rasters", f"{name}.tif")
        if path.exists(potential_raster):
            try:
                os.remove(potential_raster)
            except Exception as e:
                print(f"Kon raster voor {name} niet verwijderen: {e}")

        self.gdf = self.gdf[self.gdf['name'] != name]

        self.save()

    @property
    def context(self) -> dict:
        """Geeft de volledige feature collection voor de webapp."""
        if self.gdf is None or self.gdf.empty:
            return {"type": "FeatureCollection", "features": []}
        return json.loads(self.gdf.to_json())

    def get_layer_context(self, layer_name: str) -> dict:
        """Handig om specifiek de geofence of het traject eruit te vissen voor de webapp."""
        if self.gdf is not None and not self.gdf.empty:
            subset = self.gdf[self.gdf['name'] == layer_name]
            if not subset.empty:
                return json.loads(subset.to_json())
        return {}
    
    @property
    def geometry(self):
        """Geeft de shapely geometrie terug voor interne berekeningen."""
        if self.gdf is not None and not self.gdf.empty:
            return self.gdf.geometry.iloc[0] if len(self.gdf) == 1 else self.gdf.geometry.tolist()
        return None
    
    def update_to_raster_ref(self, name: str, raster_path: str, bounds: tuple = None):
        """
        Voegt de referentie naar de GeoTIFF toe aan de bestaande rij in de GeoDataFrame.
        Let op: We laten 'geometry' intact zodat je deze in de UI nog kunt bewerken!
        """
        if self.gdf is None or 'name' not in self.gdf.columns:
             self.load()

        if not self.gdf.empty and name in self.gdf['name'].values:
            idx = self.gdf.index[self.gdf['name'] == name].tolist()[0]
            # We zetten geometry NIET meer op None, we behouden de vectoren!
            self.gdf.at[idx, 'raster_source'] = raster_path
            
            if bounds is not None:
                self.gdf.at[idx, 'bounds'] = str(bounds)
            else:
                self.gdf.at[idx, 'bounds'] = None
        else:
            print(f"[GeoJson] Waarschuwing: {name} niet gevonden in de data. Voeg deze eerst toe via de FieldManager.")
            
        self.save()


    def save_as_raster(self, name: str, data: Any, resolution: float, properties: dict = None, epsg: int = 0, type: Any = None):
        """
        Converteert een vorm (Geofence, Traject of Task) naar een GeoTIFF raster en slaat de link op.
        """
        geom_list = self.to_shapely(data, type)
        clean_geoms = [g for g in geom_list if g is not None]
        
        if not clean_geoms:
            print(f"[GeoJson] Waarschuwing: Geen geldige geometrie voor {name}")
            return

        shapely_geom = unary_union(clean_geoms)

        field_bounds = None
        if self.gdf is not None and not self.gdf.empty and 'name' in self.gdf.columns:
            geofence_row = self.gdf[self.gdf['name'] == 'geofence']
            if not geofence_row.empty:
                field_bounds = geofence_row.geometry.iloc[0].bounds
        
        if field_bounds is None:
            print(f"[GeoJson] Geen geofence gevonden, we gebruiken de bounds van de vorm zelf.")
            field_bounds = shapely_geom.bounds

        raster_rel_path = f"rasters/{name}.tif"
        full_path = os.path.join(self.folder_path, raster_rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        input_crs = f"EPSG:{epsg}" if epsg else "EPSG:4326"

        print(f"Genereren raster voor {name}... Field bounds: {field_bounds}")

        raster_array, transform, width, height = rstr.generate_array(
           geometry=shapely_geom,
           bounds=field_bounds, 
           resolution=resolution,
        )

        with rasterio.open(
            full_path,
            'w',
            driver='GTiff',
            height=height,
            width=width,
            count=1,
            dtype=raster_array.dtype,
            crs=input_crs,
            transform=transform,
        ) as dst:
            dst.write(raster_array, 1)

        self.update_to_raster_ref(name, raster_rel_path, bounds=field_bounds)
        
        if properties:
             idx = self.gdf.index[self.gdf['name'] == name].tolist()[0]
             for key, value in properties.items():
                 self.gdf.at[idx, key] = value
             self.save()