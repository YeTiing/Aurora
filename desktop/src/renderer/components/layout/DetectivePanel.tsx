import React, { useState } from "react";
import { useStore } from "../../store";

export function DetectivePanel({ onClose }: { onClose: () => void }) {
    const colors = useStore((s) => s.themeColors);
    const [file, setFile] = useState("");
    const [lines, setLines] = useState("");
    const [bug, setBug] = useState("");
    const [result, setResult] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const API = "http://127.0.0.1:9876";

    const analyze = async () => {
        if (!file.trim()) { setError("请输入文件路径"); return; }
        setLoading(true); setError("");
        try {
            const r = await fetch(API+"/detective/analyze", {
                method:"POST", headers:{"Content-Type":"application/json"},
                body:JSON.stringify({file,lines,bug:bug||"Bug investigation",workspace:"."})
            });
            setResult(await r.json());
        } catch(e:any) { setError(e.message); }
        setLoading(false);
    };

    const blame = async () => {
        if (!file.trim()) { setError("请输入文件路径"); return; }
        setLoading(true); setError("");
        try {
            const q = API+"/detective/blame?file="+encodeURIComponent(file)+(lines?"&lines="+encodeURIComponent(lines):"");
            setResult(await (await fetch(q)).json());
        } catch(e:any) { setError(e.message); }
        setLoading(false);
    };

    return (
        <div className="aurora-overlay">
            <div className="aurora-panel" style={{backgroundColor:colors.surface,borderColor:colors.border,color:colors.text,width:800,maxWidth:"94vw",maxHeight:"85vh",display:"flex",flexDirection:"column"}}>
                <div className="aurora-panel-header" style={{borderColor:colors.border}}>
                    <span>源码差异神探局 (Diff Detective)</span>
                    <button onClick={onClose} style={{color:colors.textSecondary,background:"none",border:"none",cursor:"pointer",fontSize:16}}>X</button>
                </div>
                <div style={{padding:12,display:"flex",flexDirection:"column",gap:8,flex:1,overflow:"auto"}}>
                    <div style={{display:"flex",gap:6}}>
                        <input value={file} onChange={e=>setFile(e.target.value)} placeholder="目标审查文件路径" style={{flex:1,background:colors.bg,color:colors.text,border:`1px solid ${colors.border}`,borderRadius:6,padding:"6px 10px",fontSize:12}} />
                        <input value={lines} onChange={e=>setLines(e.target.value)} placeholder="审查上下文行数" style={{width:160,background:colors.bg,color:colors.text,border:`1px solid ${colors.border}`,borderRadius:6,padding:"6px 10px",fontSize:12}} />
                    </div>
                    <input value={bug} onChange={e=>setBug(e.target.value)} placeholder="描述问题特征或审查需求..." style={{background:colors.bg,color:colors.text,border:`1px solid ${colors.border}`,borderRadius:6,padding:"6px 10px",fontSize:12}} />
                    <div style={{display:"flex",gap:6}}>
                        <button onClick={analyze} disabled={loading} style={{background:colors.accent,color:"#fff",border:"none",borderRadius:6,padding:"6px 14px",fontSize:12,cursor:"pointer"}}>深度查案</button>
                        <button onClick={blame} disabled={loading} style={{background:"transparent",color:colors.accent,border:`1px solid ${colors.accent}`,borderRadius:6,padding:"6px 14px",fontSize:12,cursor:"pointer"}}>仅查责任人(Blame)</button>
                    </div>
                    {error && <div style={{color:colors.error,fontSize:12,padding:6,background:colors.bg,borderRadius:4}}>{error}</div>}

                    {result && (
                        <div style={{fontSize:12}}>
                            {result.root_cause_hypothesis && (
                                <div style={{marginBottom:14,background:colors.accent+"12",padding:12,borderRadius:8}}>
                                    <div style={{fontWeight:600,color:colors.accent,marginBottom:4}}>Root Cause Hypothesis</div>
                                    <pre style={{whiteSpace:"pre-wrap",fontSize:11,color:colors.text,margin:0}}>{result.root_cause_hypothesis}</pre>
                                </div>
                            )}
                            {result.suspicious_lines&&result.suspicious_lines.length>0 && (
                                <div style={{marginBottom:14}}>
                                    <div style={{fontWeight:600,marginBottom:4,color:colors.warning}}>Suspicious Lines</div>
                                    {result.suspicious_lines.slice(0,20).map((sl:any,i:number)=>(
                                        <div key={i} style={{display:"flex",gap:8,padding:"3px 0",borderBottom:`1px solid ${colors.border}`}}>
                                            <span style={{color:colors.textSecondary,minWidth:36}}>L{sl.line}</span>
                                            <span style={{flex:1,fontFamily:"monospace",fontSize:11}}>{sl.content}</span>
                                            <span style={{color:colors.accent,minWidth:60,textAlign:"right",fontSize:10}}>{sl.commit} by {sl.author}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                            {result.suspect_commits&&result.suspect_commits.length>0 && (
                                <div>
                                    <div style={{fontWeight:600,marginBottom:4,color:colors.error}}>Suspect Commits</div>
                                    {result.suspect_commits.map((sc:any,i:number)=>(
                                        <div key={i} style={{padding:"4px 0",fontFamily:"monospace",fontSize:11}}>
                                            <span style={{color:colors.accent}}>{sc.hash}</span>{" "}
                                            <span style={{color:colors.textSecondary}}>{sc.message}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
