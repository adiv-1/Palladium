import type {StyleSpecification} from 'maplibre-gl';

// Palladium's OSM extrusion style, adapted to the existing MapLibre renderer.
// Online cartography is context; analysis and walking topology come from our bundle.
export function campusMapStyle(): StyleSpecification {
  return {version:8, glyphs:'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    sources:{omt:{type:'vector',url:'https://tiles.openfreemap.org/planet',attribution:'<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> · <a href="https://www.openmaptiles.org/" target="_blank">OpenMapTiles</a> · © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap contributors</a>'}},
    layers:[
      {id:'background',type:'background',paint:{'background-color':'#121417'}},
      {id:'landuse',type:'fill',source:'omt','source-layer':'landuse',paint:{'fill-color':'#181b20'}},
      {id:'park',type:'fill',source:'omt','source-layer':'park',paint:{'fill-color':'#1b2422'}},
      {id:'water',type:'fill',source:'omt','source-layer':'water',paint:{'fill-color':'#101820'}},
      {id:'paths',type:'line',source:'omt','source-layer':'transportation',paint:{'line-color':'#363b43','line-width':['interpolate',['linear'],['zoom'],13,.7,18,4]}},
      {id:'street-names',type:'symbol',source:'omt','source-layer':'transportation_name',minzoom:14,layout:{'text-field':['get','name'],'text-font':['Noto Sans Regular'],'text-size':11,'symbol-placement':'line'},paint:{'text-color':'#9299a3','text-halo-color':'#121417','text-halo-width':1.5}},
    ]};
}
