import geopandas as gpd

#path to the shape file
shp_path = "data/refs/ne_50m_admin_0_countries/ne_50m_admin_0_countries.shp"

#load the shape file
gdf = gpd.read_file(shp_path)


#keep just the country name, ISO code and geometry
gdf = gdf[['ADMIN', 'ISO_A3', 'geometry']]

proj_crs = "ESRI:54009"

gdf_proj = gdf.to_crs(proj_crs)

centroid_proj = gdf_proj.geometry.centroid

centroids_wgs84 = centroid_proj.to_crs(epsg=4326)

gdf['lat'] = centroids_wgs84.y
gdf['lon'] = centroids_wgs84.x

#save the centroid table
out_path = "data/refs/country_centroids.csv"
gdf[['ADMIN', 'ISO_A3', 'lat', 'lon']].to_csv(out_path, index=False)

print(f"Saved {out_path} with {len(gdf)} countries")
print(gdf[['ADMIN', 'ISO_A3', 'lat', 'lon']].head())