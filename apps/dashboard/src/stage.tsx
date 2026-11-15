import {useCallback, useEffect, useRef, useState} from 'react';
import DeckGL from '@deck.gl/react';
import {FlyToInterpolator, type Layer, type MapViewState, type PickingInfo} from '@deck.gl/core';
import {Map} from 'react-map-gl/maplibre';
import {campusStyle} from './mapStyle';
import {useTheme} from './theme';
import {CAMPUS_CENTER} from './sim';

export const HOME: MapViewState = {longitude: CAMPUS_CENTER[0], latitude: CAMPUS_CENTER[1], zoom: 16.1, pitch: 58, bearing: -20};
const fly = new FlyToInterpolator();
type View = MapViewState & Record<string, unknown>;

// Camera + animation clock shared by the map pages: slow orbit when idle, fly-to on demand.
export function useStage(orbit = true) {
  const [viewState, setViewState] = useState<View>(HOME);
  const [time, setTime] = useState(0);
  const touched = useRef(0);
  const orbitRef = useRef(orbit);
  orbitRef.current = orbit;
  useEffect(() => {
    let frame = 0, last = performance.now(), lastRender = 0;
    const start = last;
    const loop = (t: number) => {
      if (t - lastRender > 33) {
        const dt = (t - last) / 1000;
        last = t; lastRender = t;
        setTime((t - start) / 1000);
        if (orbitRef.current && Date.now() - touched.current > 6000) {
          setViewState((v) => (v.transitionDuration ? v : {...v, bearing: (v.bearing ?? 0) + 2.5 * dt}));
        }
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, []);
  const flyTo = useCallback((v: Partial<MapViewState>, ms = 1600) => {
    touched.current = Date.now();
    setViewState((c) => ({...c, ...v, transitionDuration: ms, transitionInterpolator: fly}));
    setTimeout(() => setViewState((c) => ({...c, transitionDuration: 0, transitionInterpolator: undefined})), ms + 80);
  }, []);
  const touch = useCallback(() => {touched.current = Date.now();}, []);
  return {viewState, setViewState, time, flyTo, touch};
}

export function Stage({stage, layers, tooltip, onClick}: {
  stage: ReturnType<typeof useStage>; layers: Layer[]; tooltip?: (i: PickingInfo) => {text: string} | null; onClick?: (i: PickingInfo) => void;
}) {
  const [theme] = useTheme();
  return (
    <DeckGL
      viewState={stage.viewState}
      controller={{inertia: 300}}
      layers={layers}
      getTooltip={tooltip as any}
      onClick={onClick}
      onViewStateChange={({viewState, interactionState}) => {
        if (interactionState?.isDragging || interactionState?.isZooming || interactionState?.isPanning || interactionState?.isRotating) stage.touch();
        stage.setViewState(viewState as View);
      }}
    >
      <Map mapStyle={campusStyle(theme)} reuseMaps attributionControl={{compact: true}} />
    </DeckGL>
  );
}
