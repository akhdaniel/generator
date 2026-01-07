import React, { useMemo, useState, useContext, useEffect, useRef } from "react";
import ReactFlow, {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  Panel,
  addEdge,
  getBezierPath,
  useReactFlow,
  useEdgesState,
  useNodesState
} from "reactflow";

import "reactflow/dist/style.css";

const EditorContext = React.createContext(null);

const DEFAULT_TYPES = [
  { value: "association", label: "Association" },
  { value: "inheritance", label: "Inheritance" },
  { value: "aggregation", label: "Aggregation" },
  { value: "composition", label: "Composition" },
  { value: "dependency", label: "Dependency" }
];

const MULTIPLICITY_PRESETS = [
  { value: "one2many", label: "one to many (1)", text: "1" },
  { value: "many2one", label: "many to one (0..*)", text: "0..*" },
  { value: "many2many", label: "many to many (*)", text: "*" }
];

const AUTH_STORAGE_KEY = "uml-auth";

const loadAuthState = () => {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    console.error("Failed to read auth state", err);
    return null;
  }
};

const useRemoteDiagrams = (token) => {
  const [all, setAll] = useState({});
  const apiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

  const refreshDiagrams = async () => {
    try {
      const res = await fetch(`${apiBase}/api/diagrams`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAll(data.diagrams || {});
    } catch (err) {
      console.error("Failed to load diagrams", err);
    }
  };

  useEffect(() => {
    if (token) refreshDiagrams();
  }, [apiBase, token]);

  const saveDiagram = async (name, data) => {
    try {
      if (!token) throw new Error("Not authenticated");
      const res = await fetch(`${apiBase}/api/diagrams/${encodeURIComponent(name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(data)
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAll((prev) => ({ ...prev, [name]: data }));
    } catch (err) {
      console.error("Failed to save diagram", err);
    }
  };

  const removeDiagram = async (name) => {
    try {
      if (!token) throw new Error("Not authenticated");
      const res = await fetch(`${apiBase}/api/diagrams/${encodeURIComponent(name)}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAll((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    } catch (err) {
      console.error("Failed to remove diagram", err);
    }
  };

  return { diagrams: all, saveDiagram, removeDiagram, refreshDiagrams };
};

const UmlNode = ({ id, data, selected }) => {
  const { name, attributes, methods } = data;
  const editor = useContext(EditorContext);
  const isEditing = editor?.editingNodeId === id;
  const [attrInput, setAttrInput] = useState("");
  const [methodInput, setMethodInput] = useState("");
  const [nameInput, setNameInput] = useState(name);

  useEffect(() => setNameInput(name), [name]);

  const saveName = () => {
    if (!editor) return;
    editor.renameNode(id, nameInput || "Class");
  };

  return (
    <>
      <Handle type="target" position={Position.Top} />
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <Handle type="source" position={Position.Bottom} />
      <div className={`uml-node ${selected ? "selected" : ""} ${isEditing ? "editing" : ""}`}>
        <div className="uml-header">
          {isEditing ? (
            <input
              className="inline-input"
              value={nameInput}
              onChange={(e) => setNameInput(e.target.value)}
              onBlur={saveName}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  saveName();
                  e.currentTarget.blur();
                }
              }}
            />
          ) : (
            <span>{name || "Class"}</span>
          )}
          {isEditing && (
            <button className="small-btn secondary" onClick={() => editor?.setEditingNodeId(null)}>
              Done
            </button>
          )}
        </div>
        <div className="uml-section">
          <div className="muted">Attributes</div>
          <ul>
            {attributes.length === 0 && <li className="muted">No attributes</li>}
            {attributes.map((attr, idx) => (
              <li key={idx}>
                {attr}
                {isEditing && (
                  <button
                    className="small-btn secondary tiny"
                    onClick={() => editor?.removeAttribute(id, idx)}
                  >
                    x
                  </button>
                )}
              </li>
            ))}
          </ul>
          {isEditing && (
            <div className="inline-input-row">
              <input
                className="inline-input"
                type="text"
                value={attrInput}
                onChange={(e) => setAttrInput(e.target.value)}
                placeholder="status: string"
              />
              <button
                className="small-btn"
                onClick={() => {
                  editor?.addAttribute(id, attrInput);
                  setAttrInput("");
                }}
              >
                Add
              </button>
            </div>
          )}
        </div>
        <div className="uml-section">
          <div className="muted">Methods</div>
          <ul>
            {methods.length === 0 && <li className="muted">No methods</li>}
            {methods.map((m, idx) => (
              <li key={idx}>
                {m}
                {isEditing && (
                  <button
                    className="small-btn secondary tiny"
                    onClick={() => editor?.removeMethod(id, idx)}
                  >
                    x
                  </button>
                )}
              </li>
            ))}
          </ul>
          {isEditing && (
            <div className="inline-input-row">
              <input
                className="inline-input"
                type="text"
                value={methodInput}
                onChange={(e) => setMethodInput(e.target.value)}
                placeholder="calculateTotal()"
              />
              <button
                className="small-btn"
                onClick={() => {
                  editor?.addMethod(id, methodInput);
                  setMethodInput("");
                }}
              >
                Add
              </button>
            </div>
          )}
        </div>
        {isEditing && (
          <div className="inline-actions">
            <button className="small-btn danger" onClick={() => editor?.deleteNode(id)}>
              Delete Class
            </button>
          </div>
        )}
      </div>
    </>
  );
};

const getMarkerForType = (type) => {
  switch (type) {
    case "inheritance":
      return { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 24, height: 16 };
    case "aggregation":
      return { type: MarkerType.ArrowClosed, color: "var(--accent-2)", width: 20, height: 12 };
    case "composition":
      return { type: MarkerType.ArrowClosed, color: "var(--accent-2)", width: 20, height: 12 };
    default:
      return { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 20, height: 12 };
  }
};

const getStrokeForType = (type) => (type === "dependency" ? "6 4" : undefined);

const getMarkersForType = (type) => {
  const arrow = { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 20, height: 12 };
  const arrowHollow = { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 24, height: 16 };
  const diamondHollow = { type: MarkerType.ArrowClosed, color: "var(--accent-2)", width: 20, height: 12 };
  const diamondFilled = { type: MarkerType.ArrowClosed, color: "var(--accent-2)", width: 20, height: 12 };

  switch (type) {
    case "inheritance":
      return { markerStart: undefined, markerEnd: arrowHollow };
    case "aggregation":
      return { markerStart: diamondHollow, markerEnd: arrow };
    case "composition":
      return { markerStart: diamondFilled, markerEnd: arrow };
    case "dependency":
      return { markerStart: undefined, markerEnd: arrow };
    default:
      return { markerStart: undefined, markerEnd: arrow };
  }
};

const getNodeIntersection = (intersectionNode, targetNode) => {
  const {
    positionAbsolute: paSource,
    width: sourceWidth = 0,
    height: sourceHeight = 0
  } = intersectionNode;
  const {
    positionAbsolute: paTarget,
    width: targetWidth = 0,
    height: targetHeight = 0
  } = targetNode;

  const sourceX = (paSource?.x || 0) + sourceWidth / 2;
  const sourceY = (paSource?.y || 0) + sourceHeight / 2;
  const targetX = (paTarget?.x || 0) + targetWidth / 2;
  const targetY = (paTarget?.y || 0) + targetHeight / 2;

  const dx = targetX - sourceX;
  const dy = targetY - sourceY;

  const absDx = Math.abs(dx);
  const absDy = Math.abs(dy);

  const w = sourceWidth / 2;
  const h = sourceHeight / 2;

  let offsetX = 0;
  let offsetY = 0;

  if (absDx / w > absDy / h) {
    offsetX = dx > 0 ? w : -w;
    offsetY = (absDy * offsetX) / absDx;
  } else {
    offsetY = dy > 0 ? h : -h;
    offsetX = (absDx * offsetY) / absDy;
  }

  return { x: sourceX + offsetX, y: sourceY + offsetY };
};

const getEdgePosition = (node, intersectionPoint) => {
  const n = node;
  const nx = n.positionAbsolute?.x || 0;
  const ny = n.positionAbsolute?.y || 0;
  const halfW = (n.width || 0) / 2;
  const halfH = (n.height || 0) / 2;
  const x = nx + halfW;
  const y = ny + halfH;
  const px = intersectionPoint.x;
  const py = intersectionPoint.y;
  const dx = px - x;
  const dy = py - y;
  const absDx = Math.abs(dx);
  const absDy = Math.abs(dy);

  if (absDx > absDy) {
    return dx > 0 ? Position.Right : Position.Left;
  }
  return dy > 0 ? Position.Bottom : Position.Top;
};

const getFloatingEdgeParams = (sourceNode, targetNode) => {
  const sourceIntersectionPoint = getNodeIntersection(sourceNode, targetNode);
  const targetIntersectionPoint = getNodeIntersection(targetNode, sourceNode);

  const sourcePos = getEdgePosition(sourceNode, sourceIntersectionPoint);
  const targetPos = getEdgePosition(targetNode, targetIntersectionPoint);

  return {
    sourceX: sourceIntersectionPoint.x,
    sourceY: sourceIntersectionPoint.y,
    targetX: targetIntersectionPoint.x,
    targetY: targetIntersectionPoint.y,
    sourcePosition: sourcePos,
    targetPosition: targetPos
  };
};

const DIAMOND_MARKER_ID = "uml-empty-diamond";
const DIAMOND_MARKER_SELECTED_ID = "uml-empty-diamond-selected";
const DIAMOND_MARKER_LENGTH = 7.5;

const EmptyDiamondMarker = ({ id, color }) => (
  <defs>
    <marker
      id={id}
      markerWidth="7.5"
      markerHeight="7.5"
      refX="1"
      refY="3.75"
      orient="auto"
      markerUnits="strokeWidth"
    >
      <path d="M 3.75 0 L 7.5 3.75 L 3.75 7.5 L 0 3.75 Z" fill="none" stroke={color} strokeWidth="1.2" />
    </marker>
  </defs>
);

const offsetEdgeEnd = (sourceX, sourceY, targetX, targetY, offset) => {
  const dx = targetX - sourceX;
  const dy = targetY - sourceY;
  const length = Math.hypot(dx, dy);
  if (length === 0 || offset <= 0 || offset >= length) {
    return { targetX, targetY };
  }
  const ratio = (length - offset) / length;
  return {
    targetX: sourceX + dx * ratio,
    targetY: sourceY + dy * ratio
  };
};

const LabeledEdge = (props) => {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    style,
    data,
    markerEnd,
    markerStart,
    selected
  } = props;

  const labelFromPreset = (preset) =>
    MULTIPLICITY_PRESETS.find((p) => p.value === preset)?.text || preset || "";

  const edgeStyle = {
    ...style,
    stroke: selected ? "var(--accent-2)" : style?.stroke || "var(--accent)",
    strokeWidth: selected ? 3 : style?.strokeWidth || 2
  };

  const markerOffset = DIAMOND_MARKER_LENGTH * (edgeStyle.strokeWidth || 1) * 0.6;
  const { targetX: adjustedTargetX, targetY: adjustedTargetY } = offsetEdgeEnd(
    sourceX,
    sourceY,
    targetX,
    targetY,
    markerOffset
  );

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX: adjustedTargetX,
    targetY: adjustedTargetY,
    sourcePosition,
    targetPosition
  });

  const sourceLabelPos = {
    x: sourceX * 0.85 + targetX * 0.15,
    y: sourceY * 0.85 + targetY * 0.15
  };

  const targetLabelPos = {
    x: sourceX * 0.15 + adjustedTargetX * 0.85,
    y: sourceY * 0.15 + adjustedTargetY * 0.85
  };

  const markerId = selected ? DIAMOND_MARKER_SELECTED_ID : DIAMOND_MARKER_ID;
  const markerColor = selected ? "var(--accent-2)" : "var(--accent)";

  return (
    <>
      <EmptyDiamondMarker id={markerId} color={markerColor} />
      <BaseEdge id={id} path={edgePath} markerEnd={`url(#${markerId})`} style={edgeStyle} />
      <EdgeLabelRenderer>
        {(data?.sourceMultiplicity || data?.sourceLabel || data?.sourceRole) && (
          <div
            className="edge-label small"
            style={{
              transform: `translate(-50%, -50%) translate(${sourceLabelPos.x}px,${sourceLabelPos.y}px)`,
              background: selected ? "rgba(34,211,238,0.25)" : "rgba(34,211,238,0.12)",
              borderColor: selected ? "var(--accent-2)" : "var(--border)"
            }}
          >
            <div className="edge-label-mult">{labelFromPreset(data.sourceMultiplicity) || data.sourceLabel}</div>
            {data.sourceRole && <div className="edge-label-role">{data.sourceRole}</div>}
          </div>
        )}
        {data?.label && (
          <div
            className="edge-label"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              borderColor: selected ? "var(--accent-2)" : "var(--border)"
            }}
          >
            {data.label}
          </div>
        )}
        {(data?.targetMultiplicity || data?.targetLabel || data?.targetRole) && (
          <div
            className="edge-label small"
            style={{
              transform: `translate(-50%, -50%) translate(${targetLabelPos.x}px,${targetLabelPos.y}px)`,
              background: selected ? "rgba(124,58,237,0.25)" : "rgba(124,58,237,0.12)",
              borderColor: selected ? "var(--accent)" : "var(--border)"
            }}
          >
            <div className="edge-label-mult">{labelFromPreset(data.targetMultiplicity) || data.targetLabel}</div>
            {data.targetRole && <div className="edge-label-role">{data.targetRole}</div>}
          </div>
        )}
      </EdgeLabelRenderer>
    </>
  );
};

const FloatingLabeledEdge = (props) => {
  const { id, source, target, style, data, markerEnd, markerStart, selected } = props;
  const { getNode } = useReactFlow();
  const sourceNode = getNode(source);
  const targetNode = getNode(target);

  if (!sourceNode || !targetNode) {
    return <LabeledEdge {...props} />;
  }

  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition } =
    getFloatingEdgeParams(sourceNode, targetNode);

  const labelFromPreset = (preset) =>
    MULTIPLICITY_PRESETS.find((p) => p.value === preset)?.text || preset || "";

  const edgeStyle = {
    ...style,
    stroke: selected ? "var(--accent-2)" : style?.stroke || "var(--accent)",
    strokeWidth: selected ? 3 : style?.strokeWidth || 2
  };

  const markerId = selected ? DIAMOND_MARKER_SELECTED_ID : DIAMOND_MARKER_ID;
  const markerColor = selected ? "var(--accent-2)" : "var(--accent)";

  const markerOffset = DIAMOND_MARKER_LENGTH * (edgeStyle.strokeWidth || 1) * 0.6;
  const { targetX: adjustedTargetX, targetY: adjustedTargetY } = offsetEdgeEnd(
    sourceX,
    sourceY,
    targetX,
    targetY,
    markerOffset
  );

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX: adjustedTargetX,
    targetY: adjustedTargetY,
    sourcePosition,
    targetPosition
  });

  const sourceLabelPos = {
    x: sourceX * 0.85 + targetX * 0.15,
    y: sourceY * 0.85 + targetY * 0.15
  };

  const targetLabelPos = {
    x: sourceX * 0.15 + adjustedTargetX * 0.85,
    y: sourceY * 0.15 + adjustedTargetY * 0.85
  };

  return (
    <>
      <EmptyDiamondMarker id={markerId} color={markerColor} />
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={`url(#${markerId})`}
        style={edgeStyle}
      />
      <EdgeLabelRenderer>
        {(data?.sourceMultiplicity || data?.sourceLabel || data?.sourceRole) && (
          <div
            className="edge-label small"
            style={{
              transform: `translate(-50%, -50%) translate(${sourceLabelPos.x}px,${sourceLabelPos.y}px)`,
              background: selected ? "rgba(34,211,238,0.25)" : "rgba(34,211,238,0.12)",
              borderColor: selected ? "var(--accent-2)" : "var(--border)"
            }}
          >
            <div className="edge-label-mult">{labelFromPreset(data.sourceMultiplicity) || data.sourceLabel}</div>
            {data.sourceRole && <div className="edge-label-role">{data.sourceRole}</div>}
          </div>
        )}
        {data?.label && (
          <div
            className="edge-label"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              borderColor: selected ? "var(--accent-2)" : "var(--border)"
            }}
          >
            {data.label}
          </div>
        )}
        {(data?.targetMultiplicity || data?.targetLabel || data?.targetRole) && (
          <div
            className="edge-label small"
            style={{
              transform: `translate(-50%, -50%) translate(${targetLabelPos.x}px,${targetLabelPos.y}px)`,
              background: selected ? "rgba(124,58,237,0.25)" : "rgba(124,58,237,0.12)",
              borderColor: selected ? "var(--accent)" : "var(--border)"
            }}
          >
            <div className="edge-label-mult">{labelFromPreset(data.targetMultiplicity) || data.targetLabel}</div>
            {data.targetRole && <div className="edge-label-role">{data.targetRole}</div>}
          </div>
        )}
      </EdgeLabelRenderer>
    </>
  );
};

const nodeTypes = { uml: UmlNode };
const edgeTypes = { labeled: LabeledEdge, floating: FloatingLabeledEdge };

const createChatMessage = (role, text) => ({
  role,
  text,
  timestamp: new Date().toLocaleString()
});

const DEFAULT_CHAT_MESSAGES = [createChatMessage("assistant", "Hi! Ask about your diagram or usage.")];

const App = () => {
  const [auth, setAuth] = useState(loadAuthState);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authMode, setAuthMode] = useState("login");
  const [authForm, setAuthForm] = useState({ email: "", password: "" });
  const [authError, setAuthError] = useState("");
  const { diagrams, saveDiagram, removeDiagram, refreshDiagrams } = useRemoteDiagrams(auth?.token);
  const [diagramName, setDiagramName] = useState("My Diagram");
  const [showOpenModal, setShowOpenModal] = useState(false);
  const [relationForm, setRelationForm] = useState({
    from: "",
    to: "",
    type: "association",
    label: "",
    sourceMultiplicity: "many2one",
    targetMultiplicity: "one2many",
    sourceRole: "",
    targetRole: ""
  });
  const [connectStart, setConnectStart] = useState(null);
  const [editingNodeId, setEditingNodeId] = useState(null);
  const [chatMessages, setChatMessages] = useState(DEFAULT_CHAT_MESSAGES);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const chatEndRef = useRef(null);
  const layoutRef = useRef(null);
  const [diagramWidth, setDiagramWidth] = useState(70);
  const isResizingRef = useRef(false);

  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNodeId, setSelectedNodeId] = useState(null);

  const saveAuthState = (nextAuth) => {
    setAuth(nextAuth);
    if (!nextAuth) {
      localStorage.removeItem(AUTH_STORAGE_KEY);
    } else {
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(nextAuth));
    }
  };

  const handleAuthSubmit = async () => {
    setAuthError("");
    const email = authForm.email.trim();
    const password = authForm.password.trim();
    if (!email || !password) {
      setAuthError("Email and password required");
      return;
    }
    const apiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
    const endpoint = authMode === "signup" ? "/api/auth/signup" : "/api/auth/login";
    try {
      const res = await fetch(`${apiBase}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      saveAuthState({ token: data.token, email: data.email });
      setAuthModalOpen(false);
      setAuthForm({ email: "", password: "" });
    } catch (err) {
      setAuthError(err.message || "Authentication failed");
    }
  };

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chatMessages]);

  useEffect(() => {
    const handleMouseMove = (event) => {
      if (!isResizingRef.current || !layoutRef.current) return;
      const rect = layoutRef.current.getBoundingClientRect();
      const offsetX = event.clientX - rect.left;
      const nextPercent = (offsetX / rect.width) * 100;
      const clamped = Math.min(85, Math.max(40, nextPercent));
      setDiagramWidth(clamped);
    };

    const handleMouseUp = () => {
      isResizingRef.current = false;
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, []);

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  const addClass = () => {
    const name = `Class ${nodes.length + 1}`;
    const id = `cls-${Date.now()}`;
    const newNode = {
      id,
      type: "uml",
      position: { x: 100 + nodes.length * 40, y: 100 + nodes.length * 30 },
      data: { name, attributes: [], methods: [] }
    };
    setNodes((prev) => [...prev, newNode]);
    setSelectedNodeId(id);
    setEditingNodeId(id);
  };

  const updateNodeData = (id, updater) => {
    setNodes((prev) =>
      prev.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...updater(n.data) } } : n))
    );
  };

  const deleteNode = (id) => {
    setNodes((prev) => prev.filter((n) => n.id !== id));
    setEdges((prev) => prev.filter((e) => e.source !== id && e.target !== id));
    setSelectedNodeId(null);
    setEditingNodeId(null);
  };

  const addRelation = () => {
    const { from, to, type, label, sourceMultiplicity, targetMultiplicity, sourceRole, targetRole } =
      relationForm;
    if (!from || !to || from === to) return;
    addRelationEdge(from, to, { type, label, sourceMultiplicity, targetMultiplicity, sourceRole, targetRole });
  };

  const getNodeName = (id) => nodes.find((n) => n.id === id)?.data.name || "";

  const addRelationEdge = (
    from,
    to,
    { type, label, sourceMultiplicity, targetMultiplicity, sourceRole, targetRole }
  ) => {
    const sourceName = getNodeName(from);
    const targetName = getNodeName(to);
    const defaultSourceRole = `${targetName || "target"}_id`;
    const defaultTargetRole = `${sourceName || "source"}_ids`;

    const resolvedSourceMultiplicity = sourceMultiplicity || "many2one";
    const resolvedTargetMultiplicity = targetMultiplicity || "one2many";
    const resolvedSourceRole = (sourceRole || "").trim() || defaultSourceRole;
    const resolvedTargetRole = (targetRole || "").trim() || defaultTargetRole;

    const { markerStart, markerEnd } = getMarkersForType(type);
    const style = {
      strokeDasharray: getStrokeForType(type),
      strokeWidth: 2,
      stroke: "var(--accent)"
    };
    setEdges((eds) =>
      addEdge(
        {
          id: `rel-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`,
          source: from,
          target: to,
          type: "floating",
          data: {
            label: (label || "").trim(),
            sourceMultiplicity: resolvedSourceMultiplicity,
            targetMultiplicity: resolvedTargetMultiplicity,
            sourceRole: resolvedSourceRole,
            targetRole: resolvedTargetRole,
            relationType: type
          },
          markerStart,
          markerEnd,
          style
        },
        eds
      )
    );
  };

  const saveCurrent = () => {
    if (!auth?.token) {
      setAuthMode("login");
      setAuthError("Please log in to save diagrams");
      setAuthModalOpen(true);
      return;
    }
    const payload = {
      nodes,
      edges,
      chatMessages
    };
    saveDiagram(diagramName || "Untitled", payload);
  };

  const loadDiagram = (name) => {
    const data = diagrams[name];
    if (!data) return;
    setNodes(data.nodes || []);
    setEdges(data.edges || []);
    setSelectedNodeId(null);
    setEditingNodeId(null);
    setDiagramName(name);
    setChatMessages(data.chatMessages || DEFAULT_CHAT_MESSAGES);
  };

  const clearAll = () => {
    setNodes([]);
    setEdges([]);
    setSelectedNodeId(null);
    setChatMessages(DEFAULT_CHAT_MESSAGES);
  };

  const addAttributeToNode = (id, val) => {
    if (!val.trim()) return;
    updateNodeData(id, (d) => ({ attributes: [...d.attributes, val.trim()] }));
  };

  const addMethodToNode = (id, val) => {
    if (!val.trim()) return;
    updateNodeData(id, (d) => ({ methods: [...d.methods, val.trim()] }));
  };

  const removeAttributeFromNode = (id, idx) => {
    updateNodeData(id, (d) => ({
      attributes: d.attributes.filter((_, i) => i !== idx)
    }));
  };

  const removeMethodFromNode = (id, idx) => {
    updateNodeData(id, (d) => ({
      methods: d.methods.filter((_, i) => i !== idx)
    }));
  };

  const renameNode = (id, name) => {
    updateNodeData(id, () => ({ name: name || "Class" }));
  };

  const rearrangeNodes = (list) => {
    const nodesToArrange = Array.isArray(list) ? list : [];
    if (nodesToArrange.length === 0) return nodesToArrange;
    const cols = Math.max(1, Math.ceil(Math.sqrt(nodesToArrange.length)));
    const startX = 120;
    const startY = 120;
    const gapX = 460;
    const gapY = 340;
    return nodesToArrange.map((node, index) => {
      const col = index % cols;
      const row = Math.floor(index / cols);
      return {
        ...node,
        position: { x: startX + col * gapX, y: startY + row * gapY }
      };
    });
  };

  const parseDiagramPayload = (text) => {
    if (!text) return null;
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object") return null;
      if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) return null;
      return parsed;
    } catch (err) {
      return null;
    }
  };

  const buildChatMessage = (text) => {
    const trimmed = text.trim();
    if (!trimmed) return trimmed;
    const diagramContext = JSON.stringify({ nodes, edges });
    return `${trimmed}\n\nCURRENT_DIAGRAM_JSON:\n${diagramContext}`;
  };

  const normalizeDiagramEdges = (diagram) => {
    const nodeNameById = new Map((diagram.nodes || []).map((node) => [node.id, node.data?.name || ""]));
    const buildRoleDefaults = (sourceId, targetId) => {
      const sourceName = nodeNameById.get(sourceId) || "source";
      const targetName = nodeNameById.get(targetId) || "target";
      return {
        sourceRole: `${targetName || "target"}_id`,
        targetRole: `${sourceName || "source"}_ids`
      };
    };

    const normalizedEdges = (diagram.edges || []).map((edge) => {
      const data = edge.data || {};
      const defaults = buildRoleDefaults(edge.source, edge.target);
      return {
        ...edge,
        data: {
          ...data,
          relationType: data.relationType || "association",
          sourceMultiplicity: data.sourceMultiplicity || "many2one",
          targetMultiplicity: data.targetMultiplicity || "one2many",
          sourceRole: (data.sourceRole || "").trim() || defaults.sourceRole,
          targetRole: (data.targetRole || "").trim() || defaults.targetRole,
          label: (data.label || "").trim()
        }
      };
    });

    return { ...diagram, edges: normalizedEdges };
  };

  const sendChat = async () => {
    if (!chatInput.trim() || chatLoading) return;
    const userMsg = createChatMessage("user", chatInput.trim());
    setChatMessages((prev) => [...prev, userMsg]);
    setChatInput("");
    setChatLoading(true);

    const apiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
    try {
      const messageWithContext = buildChatMessage(userMsg.text);
      const res = await fetch(`${apiBase}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: messageWithContext, conversation_id: conversationId })
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConversationId(data.conversation_id || conversationId);
      const replyText = data.reply || "";
      const diagram = parseDiagramPayload(replyText);
      let assistantText = replyText;
      if (diagram) {
        const isNewDiagram = nodes.length === 0 && edges.length === 0;
        const userRequest = userMsg.text.trim();
        assistantText = isNewDiagram
          ? "Diagram Created"
          : `Diagram Updated: ${userRequest || "Applied changes"}`;
        const normalized = normalizeDiagramEdges(diagram);
        setNodes(rearrangeNodes(normalized.nodes));
        setEdges(normalized.edges);
        setSelectedNodeId(null);
        setEditingNodeId(null);
      }
      setChatMessages((prev) => [...prev, createChatMessage("assistant", assistantText)]);
    } catch (err) {
      setChatMessages((prev) => [
        ...prev,
        createChatMessage("assistant", `Backend error: ${err.message || err}`)
      ]);
    } finally {
      setChatLoading(false);
    }
  };

  if (!auth?.token) {
    return (
      <div className="auth-gate">
        <div className="card auth-card">
          <h1>UML Class Diagrammer</h1>
          <p className="muted">Log in to access your diagrams.</p>
          <div className="auth-actions">
            <button
              className="small-btn secondary"
              onClick={() => {
                setAuthMode("login");
                setAuthError("");
                setAuthModalOpen(true);
              }}
            >
              Login
            </button>
            <button
              className="small-btn"
              onClick={() => {
                setAuthMode("signup");
                setAuthError("");
                setAuthModalOpen(true);
              }}
            >
              Sign Up
            </button>
          </div>
        </div>
        {authModalOpen && (
          <div className="modal-backdrop" onClick={() => setAuthModalOpen(false)}>
            <div className="modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <h3>{authMode === "signup" ? "Create Account" : "Login"}</h3>
                <button className="small-btn secondary" onClick={() => setAuthModalOpen(false)}>
                  Close
                </button>
              </div>
              <div className="form-stack">
                <input
                  type="email"
                  placeholder="Email"
                  value={authForm.email}
                  onChange={(e) => setAuthForm((prev) => ({ ...prev, email: e.target.value }))}
                />
                <input
                  type="password"
                  placeholder="Password"
                  value={authForm.password}
                  onChange={(e) => setAuthForm((prev) => ({ ...prev, password: e.target.value }))}
                />
                {authError && <div className="muted error-text">{authError}</div>}
                <button className="small-btn" onClick={handleAuthSubmit}>
                  {authMode === "signup" ? "Sign Up" : "Login"}
                </button>
                <button
                  className="small-btn secondary"
                  onClick={() => {
                    setAuthMode(authMode === "signup" ? "login" : "signup");
                    setAuthError("");
                  }}
                >
                  {authMode === "signup" ? "Have an account? Login" : "New here? Sign up"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      <div className="toolbar">
        <div className="title">
          <h1>UML Class Diagrammer</h1>
        </div>
        <input
          type="text"
          value={diagramName}
          onChange={(e) => setDiagramName(e.target.value)}
          placeholder="Diagram name"
        />
        <button onClick={clearAll}>New</button>
        <button onClick={saveCurrent}>Save</button>
        <button onClick={() => setNodes((prev) => rearrangeNodes(prev))}>Rearange</button>
        <button
          onClick={() => {
            if (!auth?.token) {
              setAuthMode("login");
              setAuthError("Please log in to open diagrams");
              setAuthModalOpen(true);
              return;
            }
            refreshDiagrams();
            setShowOpenModal(true);
          }}
        >
          Open...
        </button>
        {auth?.email ? (
          <div className="auth-status">
            <span className="muted">{auth.email}</span>
            <button
              className="small-btn secondary"
              onClick={() => {
                saveAuthState(null);
                clearAll();
              }}
            >
              Logout
            </button>
          </div>
        ) : (
          <div className="auth-status">
            <button
              className="small-btn secondary"
              onClick={() => {
                setAuthMode("login");
                setAuthError("");
                setAuthModalOpen(true);
              }}
            >
              Login
            </button>
            <button
              className="small-btn"
              onClick={() => {
                setAuthMode("signup");
                setAuthError("");
                setAuthModalOpen(true);
              }}
            >
              Sign Up
            </button>
          </div>
        )}
        <button
          onClick={() => {
            if (!auth?.token) {
              setAuthMode("login");
              setAuthError("Please log in to delete diagrams");
              setAuthModalOpen(true);
              return;
            }
            if (diagramName && diagrams[diagramName]) {
              removeDiagram(diagramName);
              clearAll();
            }
          }}
          style={{ marginLeft: "auto" }}
        >
          Delete Saved
        </button>
      </div>
      <div className="layout" ref={layoutRef}>
        <div className="diagram-area" style={{ width: `${diagramWidth}%` }}>
          <EditorContext.Provider
            value={{
              editingNodeId,
              setEditingNodeId,
              addAttribute: addAttributeToNode,
              addMethod: addMethodToNode,
              removeAttribute: removeAttributeFromNode,
              removeMethod: removeMethodFromNode,
              renameNode,
              deleteNode
            }}
          >
            <ReactFlow
              nodes={nodes.map((n) =>
                n.id === editingNodeId ? { ...n, data: { ...n.data, isEditing: true } } : n
              )}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              fitView
              onNodeClick={(_, node) => setSelectedNodeId(node.id)}
              onNodeDoubleClick={(_, node) => {
                setSelectedNodeId(node.id);
                setEditingNodeId(node.id);
              }}
              panOnScroll
              selectionOnDrag
              defaultEdgeOptions={{ animated: false, type: "floating" }}
              onConnectStart={(_, params) => setConnectStart(params)}
              onConnect={({ source, target }) => {
                if (!source || !target) return;
                let src = source;
                let tgt = target;
                if (connectStart?.handleType === "target") {
                  const startId = connectStart.nodeId;
                  const otherId = startId === source ? target : source;
                  if (startId && otherId) {
                    src = startId;
                    tgt = otherId;
                  }
                }
                if (src === tgt) return;
                const {
                  type,
                  sourceMultiplicity,
                  targetMultiplicity,
                  label,
                  sourceRole,
                  targetRole
                } = relationForm;
                addRelationEdge(src, tgt, {
                  type,
                  sourceMultiplicity,
                  targetMultiplicity,
                  label: label || "",
                  sourceRole,
                  targetRole
                });
                setConnectStart(null);
              }}
            >
              <Panel position="top-right">
                <button className="fab" title="Add class" onClick={addClass}>
                  ＋
                </button>
              </Panel>
              <Background gap={24} color="rgba(255,255,255,0.08)" />
              <Controls />
              <MiniMap nodeColor="#7c3aed" maskColor="rgba(15,23,42,0.6)" />
            </ReactFlow>
          </EditorContext.Provider>
        </div>
        <div
          className="pane-resizer"
          onMouseDown={() => {
            isResizingRef.current = true;
          }}
          role="separator"
          aria-label="Resize panels"
        />
        <div className="chat-panel" style={{ width: `${100 - diagramWidth}%` }}>
          <div className="card chat-card">
            <h2>Assistant</h2>
            <div className="chat-messages">
              {chatMessages.map((m, idx) => (
                <div key={idx} className={`chat-row ${m.role}`}>
                  <div className="chat-bubble">
                    <div>{m.text}</div>
                    {m.timestamp && <div className="chat-meta">{m.timestamp}</div>}
                  </div>
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>
            <div className="chat-input-row">
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Ask a question..."
                rows={3}
                disabled={chatLoading}
              />
              <button className="small-btn" onClick={sendChat} disabled={chatLoading}>
                {chatLoading ? "Sending..." : "Send"}
              </button>
            </div>
          </div>
        </div>
      </div>
      {showOpenModal && (
        <div className="modal-backdrop" onClick={() => setShowOpenModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Open Diagram</h3>
              <button className="small-btn secondary" onClick={() => setShowOpenModal(false)}>
                Close
              </button>
            </div>
            {Object.keys(diagrams).length === 0 ? (
              <p className="muted">No saved diagrams yet.</p>
            ) : (
              <div className="list">
                {Object.keys(diagrams).map((name) => (
                  <div key={name} className="list-row">
                    <span>{name}</span>
                    <button
                      className="small-btn"
                      onClick={() => {
                        loadDiagram(name);
                        setShowOpenModal(false);
                      }}
                    >
                      Open
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
      {authModalOpen && (
        <div className="modal-backdrop" onClick={() => setAuthModalOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{authMode === "signup" ? "Create Account" : "Login"}</h3>
              <button className="small-btn secondary" onClick={() => setAuthModalOpen(false)}>
                Close
              </button>
            </div>
            <div className="form-stack">
              <input
                type="email"
                placeholder="Email"
                value={authForm.email}
                onChange={(e) => setAuthForm((prev) => ({ ...prev, email: e.target.value }))}
              />
              <input
                type="password"
                placeholder="Password"
                value={authForm.password}
                onChange={(e) => setAuthForm((prev) => ({ ...prev, password: e.target.value }))}
              />
              {authError && <div className="muted error-text">{authError}</div>}
              <button className="small-btn" onClick={handleAuthSubmit}>
                {authMode === "signup" ? "Sign Up" : "Login"}
              </button>
              <button
                className="small-btn secondary"
                onClick={() => {
                  setAuthMode(authMode === "signup" ? "login" : "signup");
                  setAuthError("");
                }}
              >
                {authMode === "signup" ? "Have an account? Login" : "New here? Sign up"}
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
  );
};

export default App;
